"""Encoded snapshot fields share one interpretation across public read families."""

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("encoding", ("date", "string", "integer", "timestamp"))
def test_encoded_daily_snapshot_selects_same_identity_and_version(
    encoding: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    database = tmp_path / "versions.duckdb"
    physical, first, second = {
        "date": ("DATE", "'2026-08-01'", "'2026-08-02'"),
        "string": ("VARCHAR", "'20260801'", "'20260802'"),
        "integer": ("BIGINT", "20260801", "20260802"),
        "timestamp": ("TIMESTAMP", "'2026-08-01 00:00:00'", "'2026-08-02 00:00:00'"),
    }[encoding]
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            f"CREATE TABLE profiles(id BIGINT, code BIGINT, point {physical}, label VARCHAR)"
        )
        connection.execute(
            f"INSERT INTO profiles VALUES (1,11,{first},'old'),(2,22,{first},'old'),"
            f"(1,11,{second},'new')"
        )
        connection.execute("CREATE TABLE links(id BIGINT, profile_id BIGINT, profile_code BIGINT)")
        connection.execute("INSERT INTO links VALUES (100,1,11)")
    parse = (
        ""
        if encoding == "date"
        else ", parse=ms.timestamp(timezone='Europe/Berlin')"
        if encoding == "timestamp"
        else ", parse=ms.strptime('%Y%m%d')"
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + "profiles=ms.entity(name='profiles', datasource=ms.ref.datasource('warehouse'), source=md.table('profiles'), primary_key=['id'], versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.profiles.point'), timezone='Asia/Shanghai'))\n"
            + f"point=ms.time_dimension_column(name='point',entity=profiles,column='point'{parse})\n"
            + "profile_id=ms.dimension_column(name='id',entity=profiles,column='id')\n"
            + "code=ms.dimension_column(name='code',entity=profiles,column='code')\n"
            + "label=ms.dimension_column(name='label',entity=profiles,column='label')\n"
            + "links=ms.entity(name='links',datasource=ms.ref.datasource('warehouse'),source=md.table('links'),primary_key=['id'])\n"
            + "profile_code=ms.dimension_column(name='profile_code',entity=links,column='profile_code')\n"
            + "link_profile_id=ms.dimension_column(name='profile_id',entity=links,column='profile_id')\n"
            + "scalar_profile=ms.relationship(name='scalar_profile',from_entity=links,to_entity=profiles,keys=[ms.join_on(link_profile_id,profile_id)])\n"
            + "profile=ms.relationship(name='profile',from_entity=links,to_entity=profiles,keys=[ms.join_on(profile_code,code)])\n",
        }
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("encoded-version", timezone="UTC")
    profiles = session.catalog.get("entity:sales.profiles")
    links_entity = session.catalog.get("entity:sales.links")
    relation = session.catalog.get("relationship:sales.profile")
    scalar_relation = session.catalog.get("relationship:sales.scalar_profile")
    label = catalog_value(session.catalog, "sales.profiles.label", kind="field")
    for hour in (0, 12):
        anchor = session.instant(datetime(2026, 8, 2, hour, tzinfo=ZoneInfo("Asia/Shanghai")))
        members = session.members(profiles, at=anchor)
        assert session.frame(members).compute().to_pandas().iloc[:, 0].tolist() == [1]
        values = session.read(members, {"label": label.at(anchor)}).compute().to_pandas()
        assert values["label"].tolist() == ["new"]
        links = session.members(links_entity)
        selected = session.members(
            links_entity, where=label.via((scalar_relation,)).at(anchor) == "new"
        )
        assert session.frame(selected).compute().to_pandas().iloc[:, 0].tolist() == [100]
        image = links.image(via=(relation,), at=anchor)
        assert session.frame(image).compute().to_pandas().iloc[:, 0].tolist() == [1]


@pytest.mark.parametrize("version", ("snapshot", "validity"))
@pytest.mark.parametrize("time_kind", ("date", "instant", "mixed", "parsed", "iana"))
def test_row_attribute_time_preserves_facts_and_explains_current_axis(
    version: str,
    time_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    database = tmp_path / "row_versions.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE profiles(tenant BIGINT,id BIGINT,point DATE,ending DATE,label VARCHAR)"
        )
        connection.execute(
            "INSERT INTO profiles VALUES (1,1,'2026-08-25','2026-09-03','A'),(1,1,'2026-09-03',NULL,'B'),(2,1,'2026-08-25',NULL,'tenant2')"
        )
        connection.execute(
            "CREATE TABLE facts(id BIGINT,tenant BIGINT,profile_id BIGINT,event_time DATE,amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (1,1,1,'2026-08-25',7),(2,1,1,'2026-09-03',11),(3,1,99,'2026-09-03',13),(4,1,1,NULL,17),(5,2,1,'2026-08-25',19)"
        )
    if time_kind in ("instant", "mixed"):
        with duckdb.connect(str(database)) as connection:
            connection.execute("SET TimeZone='UTC'")
            if time_kind == "instant":
                connection.execute("ALTER TABLE profiles ALTER COLUMN point TYPE TIMESTAMPTZ")
                connection.execute("ALTER TABLE profiles ALTER COLUMN ending TYPE TIMESTAMPTZ")
            connection.execute("ALTER TABLE facts ALTER COLUMN event_time TYPE TIMESTAMPTZ")
            if time_kind == "mixed":
                connection.execute(
                    "UPDATE facts SET event_time=TIMESTAMPTZ '2026-08-24 20:00:00+00' WHERE id=1"
                )
    if time_kind == "parsed":
        with duckdb.connect(str(database)) as connection:
            connection.execute(
                "ALTER TABLE facts ALTER COLUMN event_time TYPE VARCHAR USING strftime(event_time,'%Y%m%d')"
            )
            connection.execute("UPDATE facts SET event_time='bad' WHERE id=4")
    _row_project(
        version,
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
        "md.table('profiles')",
        "md.table('facts')",
        semantic_project_factory,
        time_kind=time_kind,
    )
    monkeypatch.chdir(tmp_path)
    if time_kind == "iana":
        with duckdb.connect(str(database)) as connection:
            connection.execute("ALTER TABLE facts ALTER COLUMN event_time TYPE TIMESTAMP")
        session = mv.session.get_or_create("iana-row-rejection", timezone="UTC")
        label = (
            session.catalog.get("dimension:sales.profiles.label")
            .via((session.catalog.get("relationship:sales.profile"),))
            .at(session.catalog.get("time_dimension:sales.facts.event_time"))
        )
        with pytest.raises(AnalysisError, match="fixed-offset"):
            session.observe(
                {"value": session.catalog.get("measure:sales.facts.amount").sum()}, by=(label,)
            ).compute()
        return
    # Encoding-specific selection and SQL remain covered for every carrier.
    # Composition and source-offline recovery share the completed frame contract.
    _assert_row_result(tmp_path, extended=time_kind == "date")
    _assert_shared_routes(monkeypatch, "duckdb", "profiles")
    if time_kind != "date":
        return
    model = tmp_path / "models/semantic/sales/models.py"
    model.write_text(
        "\n".join(
            line for line in model.read_text().splitlines() if not line.startswith("other_profile=")
        )
        + "\n"
    )
    session = mv.session.get_or_create("inferred-row-root", timezone="UTC")
    control = session.catalog.get("time_dimension:sales.facts.event_time")
    label = session.catalog.get("dimension:sales.profiles.label").at(control)
    result = session.observe(
        {"value": session.catalog.get("measure:sales.facts.amount").sum()},
        by=(label,),
        where=label == "B",
    ).compute()
    assert result.to_pandas()["value"].tolist() == [11]
    assert "sales.profile" in {
        item.path for item in result.lineage.axes[result.axes[0].token].context_origins
    }


def _row_project(
    version: str,
    datasource_model: str,
    profiles_source: str,
    facts_source: str,
    semantic_project_factory: Callable[..., SemanticProject],
    *,
    time_kind: str = "date",
) -> None:
    declaration = (
        "ms.snapshot(partition_field=ms.ref.time_dimension('sales.profiles.point'),timezone='Asia/Shanghai')"
        if version == "snapshot"
        else "ms.validity(valid_from=ms.ref.time_dimension('sales.profiles.point'),valid_to=ms.ref.time_dimension('sales.profiles.ending'),interval='closed_open',open_end=(None,),timezone='Asia/Shanghai')"
    )
    if time_kind == "instant" and version == "validity":
        declaration = declaration.replace(",timezone='Asia/Shanghai'", "")
    control_parse = (
        ",parse=ms.strptime('%Y%m%d')"
        if time_kind == "parsed"
        else ",parse=ms.timestamp(timezone='America/New_York')"
        if time_kind == "iana"
        else ""
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": datasource_model,
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"profiles=ms.entity(name='profiles',datasource=ms.ref.datasource('warehouse'),source={profiles_source},primary_key=['tenant','id'],versioning={declaration})\n"
            + "point=ms.time_dimension_column(name='point',entity=profiles,column='point')\nending=ms.time_dimension_column(name='ending',entity=profiles,column='ending')\n"
            + "label=ms.dimension_column(name='label',entity=profiles,column='label')\npid=ms.dimension_column(name='id',entity=profiles,column='id')\nptenant=ms.dimension_column(name='tenant',entity=profiles,column='tenant')\n"
            + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source={facts_source},primary_key=['id'])\n"
            + f"other_time=ms.time_dimension_column(name='other_time',entity=facts,column='event_time'{control_parse})\n"
            + f"event_time=ms.time_dimension_column(name='event_time',entity=facts,column='event_time'{control_parse})\n"
            + "amount=ms.measure_column(name='amount',entity=facts,column='amount',unit='1',additivity=ms.additive_all())\n"
            + "fid=ms.dimension_column(name='profile_id',entity=facts,column='profile_id')\nftenant=ms.dimension_column(name='tenant',entity=facts,column='tenant')\n"
            + "profile=ms.relationship(name='profile',from_entity=facts,to_entity=profiles,keys=[ms.join_on(ftenant,ptenant),ms.join_on(fid,pid)])\n"
            + "other_profile=ms.relationship(name='other_profile',from_entity=facts,to_entity=profiles,keys=[ms.join_on(ftenant,ptenant),ms.join_on(fid,pid)])\n"
            + "historical_label=ms.ref.dimension('sales.profiles.label').via((profile,)).at(event_time)\n"
            + "@ms.metric()\ndef historical_b():\n    return amount.sum().where(historical_label == 'B')\n",
        }
    )


def _assert_row_result(tmp_path: Path, *, extended: bool = True) -> None:
    session = mv.session.get_or_create("row-version", timezone="UTC")
    label = session.catalog.get("dimension:sales.profiles.label")
    control = session.catalog.get("time_dimension:sales.facts.event_time")
    route = session.catalog.get("relationship:sales.profile")
    metric = session.catalog.get("measure:sales.facts.amount").sum()
    historical = label.via((route,)).at(control)
    pending = session.observe({"value": metric}, by=(historical,))
    assert isinstance(pending.schema.axes[0].at, mv.FrameRowAttributeTime)
    complete = pending.compute()
    data = complete.to_pandas(axis_names={complete.axes[0].token: "label"})
    assert data.loc[data.label.notna()].set_index("label")["value"].to_dict() == {
        "A": 7,
        "B": 11,
        "tenant2": 19,
    }
    assert data.loc[data.label.isna(), "value"].tolist() == [30]
    selected = session.observe(
        {"value": metric}, by=(historical,), where=historical == "B"
    ).compute()
    assert selected.to_pandas()["value"].tolist() == [11]
    if not extended:
        return
    facts = session.members(session.catalog.get("entity:sales.facts"))
    with pytest.raises((AnalysisError, SemanticExpressionError), match="fixed InstantAnchor"):
        session.read(facts, {"label": historical})
    scope = session.time_scope(start="2026-08-25", end="2026-09-08")
    with pytest.raises((AnalysisError, SemanticExpressionError), match="single-root observe"):
        session.observe(
            {
                "value": session.catalog.get("measure:sales.facts.amount")
                .sum(time=control)
                .window(mv.all_history())
            },
            by=(scope, historical),
        )
    # Catalog values are project-scoped; receiving kind/path binding, not the
    # Session which acquired a Catalog, owns their closed interpretation.
    other = mv.session.get_or_create("other-row-owner", timezone="UTC")
    shared_control = other.catalog.get("time_dimension:sales.facts.event_time")
    session.observe({"value": metric}, by=(label.via((route,)).at(shared_control),))
    domain = mv.coordinates(by=(historical,), keys=(("A",), ("B",), ("tenant2",), (None,)))
    covered = session.observe({"value": metric}, by=domain).compute()
    assert covered.to_pandas()["value"].sum() == 67
    authored = session.observe(
        {"value": session.catalog.get("metric:sales.historical_b")}, by=(historical,)
    ).compute()
    assert authored.to_pandas()["value"].tolist() == [11]
    fixed_axis = complete.fix(complete.axes[0], "B").compute()
    assert fixed_axis.schema.fixed_axes[0].axis.at == complete.schema.axes[0].at
    assert fixed_axis.to_pandas()["value"].tolist() == [11]
    fixed = session.observe(
        {"value": metric},
        by=(label.via((route,)).at(session.instant("2026-09-03T00:00:00+00:00")),),
    ).compute()
    fixed_data = fixed.to_pandas()
    assert fixed_data.loc[fixed_data.iloc[:, 0] == "B", "value"].tolist() == [35]
    saved = complete.persist()
    assert (
        session.persisted_frame(saved.artifact_id).schema.axes[0].at == complete.schema.axes[0].at
    )
    code = """
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
def forbidden(*args, **kwargs):
    raise AssertionError('Cold Frame recovery accessed semantic/source state')
ms.load = forbidden
mv.Session.catalog = property(forbidden)
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1])
frame = session.persisted_frame(sys.argv[2])
at = frame.schema.axes[0].at
assert isinstance(at, mv.FrameRowAttributeTime)
assert at.field.path == 'sales.facts.event_time'
assert frame.to_pandas()['value'].sum() == 67
"""
    database = tmp_path / "row_versions.duckdb"
    offline = database.with_suffix(".offline") if database.exists() else None
    if offline is not None:
        database.rename(offline)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, session.id, saved.artifact_id],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        if offline is not None:
            offline.rename(database)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.runtime
@pytest.mark.parametrize("version", ("snapshot", "validity"))
@pytest.mark.parametrize("time_kind", ("date", "instant", "mixed"))
def test_trino_row_version_join_and_daily_statement_count(
    version: str,
    time_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    profiles = SourceData(
        "tenant BIGINT,id BIGINT,point DATE,ending DATE,label VARCHAR",
        "(1,1,DATE '2026-08-25',DATE '2026-09-03','A'),(1,1,DATE '2026-09-03',NULL,'B'),(2,1,DATE '2026-08-25',NULL,'tenant2')",
        "",
        [],
    )
    facts = SourceData(
        "id BIGINT,tenant BIGINT,profile_id BIGINT,event_time DATE,amount BIGINT",
        "(1,1,1,DATE '2026-08-25',7),(2,1,1,DATE '2026-09-03',11),(3,1,99,DATE '2026-09-03',13),(4,1,1,NULL,17),(5,2,1,DATE '2026-08-25',19)",
        "",
        [],
    )
    if time_kind in ("instant", "mixed"):
        import re
        from dataclasses import replace

        def instants(data: SourceData) -> SourceData:
            return replace(
                data,
                columns=data.columns.replace(" DATE", " TIMESTAMP(6) WITH TIME ZONE"),
                values=re.sub(r"DATE '([0-9-]+)'", r"TIMESTAMP '\1 00:00:00 UTC'", data.values),
            )

        facts = instants(facts)
        if time_kind == "instant":
            profiles = instants(profiles)
        else:
            facts = replace(
                facts,
                values=facts.values.replace(
                    "(1,1,1,TIMESTAMP '2026-08-25 00:00:00 UTC'",
                    "(1,1,1,TIMESTAMP '2026-08-24 20:00:00 UTC'",
                ),
            )
    with (
        source_case("trino", "iceberg", tmp_path, monkeypatch, profiles) as target,
        source_case("trino", "iceberg", tmp_path, monkeypatch, facts) as root,
    ):
        assert isinstance(target.source, TableSourceIR) and isinstance(root.source, TableSourceIR)
        _row_project(
            version,
            _datasource_model("trino", root, monkeypatch),
            f"md.table({target.source.table!r},database={target.source.database!r})",
            f"md.table({root.source.table!r},database={root.source.database!r})",
            semantic_project_factory,
            time_kind=time_kind,
        )
        monkeypatch.chdir(tmp_path)
        _assert_row_result(tmp_path)
        _assert_shared_routes(monkeypatch, "trino", target.source.table)
        session = mv.session.get_or_create("row-version", timezone="UTC")
        control = session.catalog.get("time_dimension:sales.facts.event_time")
        label = (
            session.catalog.get("dimension:sales.profiles.label")
            .via((session.catalog.get("relationship:sales.profile"),))
            .at(control)
        )
        metric = session.catalog.get("measure:sales.facts.amount").sum(time=control)
        trace = capture_source(monkeypatch, measure_cost=True)
        for end, expected in (("2026-08-27", 26), ("2026-09-08", 50)):
            before = len(trace.native_sql)
            days = session.time_scope(
                start="2026-08-25",
                end=end,
                timezone="Asia/Shanghai" if time_kind == "mixed" else "UTC",
            ).by(mv.grain("day"))
            frame = session.observe({"value": metric}, by=(days, label)).compute()
            assert frame.to_pandas()["value"].sum() == expected
            sql = trace.native_sql[before:]
            assert len(sql) == 1
            ast = sqlglot.parse_one(sql[0], read="trino")
            assert sum(table.name == target.source.table for table in ast.find_all(sge.Table)) == 1
            assert sum(table.name == root.source.table for table in ast.find_all(sge.Table)) == 1
            assert "UNION" not in sql[0].upper()


def _assert_shared_routes(monkeypatch: pytest.MonkeyPatch, backend: str, target_table: str) -> None:
    session = mv.session.get_or_create("row-version", timezone="UTC")
    control = session.catalog.get("time_dimension:sales.facts.event_time")
    other = session.catalog.get("time_dimension:sales.facts.other_time")
    route = session.catalog.get("relationship:sales.profile")
    label = session.catalog.get("dimension:sales.profiles.label").via((route,))
    member = session.catalog.get("dimension:sales.profiles.id").via((route,)).at(control)
    amount = session.catalog.get("measure:sales.facts.amount")
    trace = capture_source(monkeypatch)
    for second, joins in (
        (member, 1),
        (label.at(other), 2),
        (
            session.catalog.get("dimension:sales.profiles.label")
            .via((session.catalog.get("relationship:sales.other_profile"),))
            .at(control),
            2,
        ),
        (label.at(session.instant("2026-09-03T00:00:00+00:00")), 2),
    ):
        before = len(trace.native_sql)
        result = session.observe(
            {"value": amount.sum()}, by=(label.at(control), second), where=amount > 0
        ).compute()
        assert (
            result.to_pandas(
                axis_names={axis.token: f"key{i}" for i, axis in enumerate(result.axes)}
            )["value"].sum()
            == 67
        )
        sql = trace.native_sql[before:]
        assert len(sql) == 1
        ast = sqlglot.parse_one(sql[0], read=backend)
        assert sum(table.name == target_table for table in ast.find_all(sge.Table)) == joins
    before = len(trace.native_sql)
    selected = session.observe(
        {"value": amount.sum()},
        by=(label.at(control),),
        where=(amount > 0) & (label.at(control) == "B"),
    ).compute()
    assert selected.to_pandas()["value"].tolist() == [11]
    ast = sqlglot.parse_one(trace.native_sql[before], read=backend)
    assert any(
        select.args.get("where") is not None
        and not any(table.name == target_table for table in select.find_all(sge.Table))
        and any(column.name == "amount" for column in select.args["where"].find_all(sge.Column))
        for select in ast.find_all(sge.Select)
    )
    # A target-dependent OR must retain facts selected only by the target arm.
    result = session.observe(
        {"value": amount.sum()},
        by=(label.at(control),),
        where=(amount > 10) | (label.at(control) == "A"),
    ).compute()
    assert (
        result.to_pandas(axis_names={axis.token: f"key{i}" for i, axis in enumerate(result.axes)})[
            "value"
        ].sum()
        == 67
    )
