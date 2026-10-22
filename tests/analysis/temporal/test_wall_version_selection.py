"""Version endpoints consume wall membership independently of native projection."""

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.member_version import version_predicate
from marivo.datasource.timezone import DatasourceEngineTimezone
from marivo.semantic.reader import SemanticProject
from marivo.semantic.validator import normalize_target_entity, normalize_target_version_selection


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
            "('gap','2026-03-08 02:15','2026-03-08 08:00'),"
            "('after','2026-03-08 03:00','2026-03-08 08:00'),"
            "('ending','2026-03-08 01:00','2026-03-08 07:00'),"
            "('null','2026-03-08 00:00',NULL),"
            "('sentinel','2026-03-08 00:00','open')"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            f"versions=ms.entity(name='versions',datasource=ms.ref.datasource('warehouse'),source=md.table('versions'),primary_key=['id'],versioning=ms.validity(valid_from=ms.ref.time_dimension('sales.versions.begins'),valid_to=ms.ref.time_dimension('sales.versions.ends'),interval={interval!r},open_end=(None,'open')))\n"
            "begins=ms.time_dimension_column(name='begins',entity=versions,column='begins',parse=ms.timestamp(timezone='America/New_York'))\n"
            "ends=ms.time_dimension_column(name='ends',entity=versions,column='ends',parse=ms.strptime('%Y-%m-%d %H:%M',timezone='UTC'))\n",
        }
    )
    session = mv.session.create("wall-version", timezone="Asia/Shanghai")
    entity = session.catalog.entities.get("sales.versions")
    boundary = datetime.fromisoformat("2026-03-08T07:00:00+00:00")
    expected = ["after", "gap", "null", "sentinel"]
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
            reader=DatasourceEngineTimezone("UTC", timezone.utc, "explicit", "engine"),
        )
        assert table.filter(predicate).order_by("id").select("id").execute()["id"].tolist() == [
            "ending",
            "null",
            "sentinel",
        ]
    finally:
        backend.disconnect()
    # The stored value retains the backend's native gap normalization.
    projected = (
        session.read(
            population,
            {
                "begins": session.catalog.fields.get("sales.versions.begins").at(
                    session.instant(boundary)
                )
            },
        )
        .compute()
        .to_pandas()
    )
    gap = projected.loc[projected.iloc[:, 0] == "gap", ("value", "begins")].iloc[0]
    assert gap.isoformat() == "2026-03-08T07:15:00+00:00"
