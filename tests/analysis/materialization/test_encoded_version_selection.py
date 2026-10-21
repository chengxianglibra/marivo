"""Encoded snapshot fields share one interpretation across public read families."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

import marivo.analysis as mv
from marivo.semantic.reader import SemanticProject


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
    session = mv.session.create("encoded-version", timezone="UTC")
    profiles = session.catalog.entities.get("sales.profiles")
    links_entity = session.catalog.entities.get("sales.links")
    relation = session.catalog.relationships.get("sales.profile")
    scalar_relation = session.catalog.relationships.get("sales.scalar_profile")
    label = session.catalog.fields.get("sales.profiles.label")
    for hour in (0, 12):
        anchor = session.instant(datetime(2026, 8, 2, hour, tzinfo=ZoneInfo("Asia/Shanghai")))
        members = session.members(profiles, at=anchor)
        assert session.frame(members).compute().to_pandas().iloc[:, 0].tolist() == [1]
        values = session.read(members, {"label": label.at(anchor)}).compute().to_pandas()
        assert values[("value", "label")].tolist() == ["new"]
        links = session.members(links_entity)
        selected = session.members(
            links_entity, where=label.via((scalar_relation,)).at(anchor) == "new"
        )
        assert session.frame(selected).compute().to_pandas().iloc[:, 0].tolist() == [100]
        image = links.image(via=(relation,), at=anchor)
        assert session.frame(image).compute().to_pandas().iloc[:, 0].tolist() == [1]
