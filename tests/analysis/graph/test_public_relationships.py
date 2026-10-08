"""Public authored relationship refs govern Metric and Event execution."""

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders", columns={
        "order_id": "order_id",
        "region": "region"}), primary_key=["order_id"])
events = ms.entity(name="events", datasource=ms.ref.datasource("warehouse"),
    source=md.table("events", columns={
        "event_id": "event_id",
        "order_id": "order_id",
        "kind": "kind",
        "occurred_at": "occurred_at"}),
    primary_key=["event_id"])
subject_key = ms.dimension_column(name="subject_key", entity=orders, column="order_id")
participant_key = ms.dimension_column(name="participant_key", entity=events, column="order_id")
region = ms.dimension_column(name="region", entity=orders, column="region")
event_key = ms.dimension_column(name="event_key", entity=events, column="event_id")
kind = ms.dimension_column(name="kind", entity=events, column="kind")
instant = ms.time_dimension_column(name="instant", entity=events, column="occurred_at",
    granularity="second", parse=ms.timestamp(timezone="UTC"), is_default=True)
event_order = ms.relationship(name="event_order", from_entity=events, to_entity=orders,
    keys=[ms.join_on(participant_key, subject_key)])
event_count = ms.count(name="event_count", entity=events, time=instant)
@ms.event(name="created", identity=(event_key,), occurred_at=instant,
    participants=(ms.participant(name="order", path=(event_order,), cardinality="one"),))
def created(rows):
    return ms.bind(kind, rows) == "created"
@ms.event(name="paid", identity=(event_key,), occurred_at=instant,
    participants=(ms.participant(name="order", path=(event_order,), cardinality="one"),))
def paid(rows):
    return ms.bind(kind, rows) == "paid"
"""


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


def _journey(session: mv.Session) -> mv.LogicalJourneyResult:
    created = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.created"), name="order"),
        key="created",
    )
    paid = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.paid"), name="order"), key="paid"
    )
    return session.events.match(
        mv.sequence(created, paid),
        population=session.members(ms.ref.entity("sales.orders")),
        cohort_window=mv.time_scope(
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
        completion_through=datetime(2026, 2, 1, tzinfo=timezone.utc),
        matching=mv.first_per_subject(),
    )


def test_authored_relationship_keys_construct_without_source_io(
    relationship_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource import backends

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("relationship resolution attempted source I/O")

    monkeypatch.setattr(backends, "build_backend", forbidden)
    monkeypatch.setattr(backends, "build_backend_with_secrets", forbidden)
    session = mv.session.get_or_create("relationship", report_timezone="UTC")
    from marivo.analysis.materialization.graph_journey import _normalize_steps

    created = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.created"), name="order"),
        key="created",
    )
    paid = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.paid"), name="order"), key="paid"
    )
    steps = _normalize_steps(session._sources(), mv.sequence(created, paid))
    assert {item.subject.ref.path for item in steps} == {"sales.orders"}
    assert session.runs().items == ()
    assert not (relationship_project / "warehouse.duckdb").exists()


@pytest.mark.runtime
def test_authored_relationship_keys_execute_metric_and_event(relationship_project: Path) -> None:
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
    session = mv.session.get_or_create("relationship", report_timezone="UTC")
    metric = (
        session.members(ms.ref.entity("sales.orders"))
        .observe(
            ms.ref.metric("sales.event_count"),
            during=mv.time_scope(start="2026-01-01", end="2026-02-01"),
            via=ms.ref.relationship("sales.event_order"),
            by=(
                ms.ref.entity("sales.orders"),
                ms.ref.dimension("sales.orders.region"),
            ),
        )
        .group_by(ms.ref.dimension("sales.orders.region"))
        .rollup()
        .execute()
        .to_pandas()
    )
    assert dict(zip(metric["group"], metric["value"], strict=True)) == {"east": 2, "west": 1}
    journeys = _journey(session).execute()
    rows = journeys.to_pandas()
    assert rows["group"].tolist() == [1, 2]
    assert rows["coord_0"].tolist() == ["sales.created", "sales.created"]
    assert rows["coord_1"].tolist() == [1, 3]
    funnel = journeys.funnel().execute().to_pandas()
    assert funnel["reached_count"].tolist() == [2, 1]
