from pathlib import Path

import duckdb

import marivo.analysis as mv


def test_version_hour_prefix_captures_consumed_prefix(
    tmp_path: Path, monkeypatch, semantic_project_factory
):
    database = tmp_path / "versions.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE TABLE profiles(id BIGINT, day DATE, hour BIGINT)")
        db.execute("INSERT INTO profiles VALUES (1,DATE '2026-07-01',0)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r},wall_clock_timezone='UTC')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/models.py": "import marivo.semantic as ms\nimport marivo.datasource as md\nprofiles=ms.entity(name='profiles',datasource=ms.ref.datasource('warehouse'),source=md.table('profiles'),primary_key=['id'],versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.profiles.hour'),timezone='UTC'))\nday=ms.time_dimension_column(name='day',entity=profiles,column='day',parse=ms.date(timezone='UTC'))\nhour=ms.time_dimension_column(name='hour',entity=profiles,column='hour',parse=ms.hour_prefix(day))\n",
        }
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("version_facts", timezone="UTC")
    members = session.members(
        session.catalog.get("entity:sales.profiles"), at=session.instant("2026-07-01T12:00:00Z")
    )
    fixed = session.frame(members).compute()
    assert fixed.to_pandas().iloc[:, 0].tolist() == [1]
    facts = fixed.schema.source_times
    print("ACTUAL_FACTS", [(f.axis, f.kind, f.physical_type) for f in facts])
    assert [f.axis for f in facts] == ["sales.profiles.day", "sales.profiles.hour"]
