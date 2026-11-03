"""Version endpoints consume the same native time interpretation as projection."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.member_version import version_predicate
from marivo.semantic.reader import SemanticProject
from marivo.semantic.validator import normalize_target_entity, normalize_target_version_selection
from tests.shared_fixtures import catalog_value


@pytest.mark.parametrize("interval", ["closed_open", "closed_closed"])
def test_validity_endpoints_keep_independent_authority_and_symbolic_before_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    interval: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "versions.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE versions (id VARCHAR, begins TIMESTAMP, ends VARCHAR)")
        connection.execute(
            "INSERT INTO versions VALUES "
            "('gap','2026-03-08 02:15','08-03-2026 08:00'),"
            "('after','2026-03-08 03:00','08-03-2026 08:00'),"
            "('ending','2026-03-08 01:00','08-03-2026 07:00'),"
            "('null','2026-03-08 00:00',NULL),"
            "('sentinel','2026-03-08 00:00','open'),"
            "('bad-end','2026-03-08 00:00','bad')"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(wall_clock_timezone='UTC', name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            f"versions=ms.entity(name='versions',datasource=ms.ref.datasource('warehouse'),source=md.table('versions'),primary_key=['id'],versioning=ms.validity(valid_from=ms.ref.time_dimension('sales.versions.begins'),valid_to=ms.ref.time_dimension('sales.versions.ends'),interval={interval!r},open_end=(None,'open')))\n"
            "begins=ms.time_dimension_column(name='begins',entity=versions,column='begins',parse=ms.timestamp(timezone='America/New_York'))\n"
            "ends=ms.time_dimension_column(name='ends',entity=versions,column='ends',parse=ms.strptime('%d-%m-%Y %H:%M',timezone='UTC'))\n",
        }
    )
    session = mv.session.get_or_create("wall-version", timezone="Asia/Shanghai")
    entity = session.catalog.get("entity:sales.versions")
    boundary = datetime.fromisoformat("2026-03-08T07:00:00+00:00")
    expected = ["after", "null", "sentinel"]
    if interval == "closed_closed":
        expected.insert(1, "ending")
    population = session.members(entity, at=session.instant(boundary))
    assert session.frame(population).compute().to_pandas().iloc[:, 0].tolist() == expected

    state = session.catalog._state
    contract = normalize_target_entity(state.registry, "sales.versions")
    selected = normalize_target_version_selection(
        contract, boundary=boundary, interpretation="before_endpoint"
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        table = backend.table("versions")
        predicate = version_predicate(
            table,
            contract.version,
            selected,
            state=state,
        )
        assert table.filter(predicate).order_by("id").select("id").execute()["id"].tolist() == [
            "ending",
            "null",
            "sentinel",
        ]
    finally:
        backend.disconnect()
    # Observe the gap record at its native normalized instant.
    later = session.instant("2026-03-08T07:15:00Z")
    population = session.members(entity, at=later)
    projected = (
        session.read(
            population,
            {
                "begins": catalog_value(session.catalog, "sales.versions.begins", kind="field").at(
                    later
                )
            },
        )
        .compute()
        .to_pandas()
    )
    gap = projected.loc[projected.iloc[:, 0] == "gap", "begins"].iloc[0]
    assert gap.isoformat() == "2026-03-08T07:15:00+00:00"
