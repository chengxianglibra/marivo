"""Public authored relationship refs govern Metric execution."""

from pathlib import Path

import duckdb
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import CoreRuleError
from marivo.semantic.errors import SemanticExpressionError
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import catalog_value, terminal_runs

_MODEL = 'import marivo.datasource as md\nimport marivo.semantic as ms\norders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"),\n    source=md.table("orders", columns={\n        "order_id": "order_id",\n        "region": "region"}), primary_key=["order_id"])\nevents = ms.entity(name="events", datasource=ms.ref.datasource("warehouse"),\n    source=md.table("events", columns={\n        "event_id": "event_id",\n        "order_id": "order_id",\n        "kind": "kind",\n        "occurred_at": "occurred_at"}),\n    primary_key=["event_id"])\nsubject_key = ms.dimension_column(name="subject_key", entity=orders, column="order_id")\nparticipant_key = ms.dimension_column(name="participant_key", entity=events, column="order_id")\nregion = ms.dimension_column(name="region", entity=orders, column="region")\nevent_key = ms.dimension_column(name="event_key", entity=events, column="event_id")\nkind = ms.dimension_column(name="kind", entity=events, column="kind")\ninstant = ms.time_dimension_column(name="instant", entity=events, column="occurred_at",\n    parse=ms.timestamp(timezone="UTC"), is_default=True)\nevent_order = ms.relationship(name="event_order", from_entity=events, to_entity=orders,\n    keys=[ms.join_on(participant_key, subject_key)])\nevents_value = events\ninstant_value = instant\n@ms.metric()\ndef event_count():\n    return events_value.count(time=instant_value)\n'


@pytest.fixture
def relationship_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "relationship-refs"\n')
    models = tmp_path / "models"
    (models / "datasources").mkdir(parents=True)
    (models / "datasources" / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        f"md.duckdb(name='warehouse', path={str(tmp_path / 'warehouse.duckdb')!r})\n"
    )
    domain = models / "semantic" / "sales"
    domain.mkdir(parents=True)
    (domain / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n"
    )
    (domain / "objects.py").write_text(_MODEL)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_authored_relationship_keys_execute_metric(relationship_project: Path) -> None:
    with duckdb.connect(str(relationship_project / "warehouse.duckdb")) as database:
        database.execute("CREATE TABLE orders (order_id BIGINT, region VARCHAR)")
        database.execute("INSERT INTO orders VALUES (1, 'east'), (2, 'west')")
        database.execute(
            "CREATE TABLE events (event_id BIGINT, order_id BIGINT, kind VARCHAR, occurred_at TIMESTAMP)"
        )
        database.execute(
            "INSERT INTO events VALUES "
            "(1, 1, 'created', TIMESTAMP '2026-01-02'), "
            "(2, 1, 'paid', TIMESTAMP '2026-01-03'), "
            "(3, 2, 'created', TIMESTAMP '2026-01-02')"
        )
    session = mv.session.get_or_create("relationship", timezone="UTC")
    route = session.catalog.get("relationship:sales.event_order").value
    region = catalog_value(session.catalog, "sales.orders.region", kind="field").via((route,))
    metric = (
        session.observe(
            {"value": session.catalog.get("metric:sales.event_count").value},
            by=(session.time_scope(start="2026-01-01", end="2026-02-01"), region),
        )
        .persist()
        .to_pandas()
    )
    assert dict(zip(metric[ordinary_columns(metric)[0]], metric["value"], strict=True)) == {
        "east": 2,
        "west": 1,
    }


def test_membership_ref_and_members_entity_have_typed_repairs(relationship_project: Path) -> None:
    session = mv.session.get_or_create("relationship-inputs", timezone="UTC")
    region = catalog_value(session.catalog, "sales.orders.region", kind="field")
    relationship_ref = ms.ref.relationship("sales.event_order")
    membership = mv.membership(region, via=((relationship_ref,),), unmatched="exclude")
    assert isinstance(membership, Deferred)
    with pytest.raises(SemanticExpressionError) as invalid_path:
        session.observe(
            {"value": session.catalog.get("metric:sales.event_count").value}, by=(membership,)
        )
    error = invalid_path.value
    assert error.expected == "closed values returned by a loaded Catalog .get()"
    assert error.received == "an authoring recipe with unbound semantic lookups"
    assert error.repair is not None
    assert "through the Catalog before analysis" in error.repair.action
    relationship = session.catalog.get("relationship:sales.event_order").value
    assert mv.membership(region, via=((relationship,),), unmatched="exclude").kind == "membership"

    with pytest.raises(CoreRuleError) as invalid_entity:
        session.members(ms.ref.entity("sales.events"))  # type: ignore[arg-type]  # Deliberately invalid public input.
    assert invalid_entity.value.expected == "a resolved Entity"
    assert invalid_entity.value.received == "Deferred"
    assert invalid_entity.value.repair is not None
    assert (
        invalid_entity.value.repair.action
        == "Resolve the Entity with session.catalog.get('entity:' + path).value after checking kind."
    )
    assert isinstance(
        session.members(session.catalog.get("entity:sales.events").value), mv.Population
    )
    assert terminal_runs(session) == ()
