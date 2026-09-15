"""Public authored relationship refs govern Metric and Event execution."""

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders", columns={
        "order_id": md.source_column("order_id", data_type="int64"),
        "region": md.source_column("region", data_type="string")}), primary_key=["order_id"])
events = ms.entity(name="events", datasource=ms.ref.datasource("warehouse"),
    source=md.table("events", columns={
        "event_id": md.source_column("event_id", data_type="int64"),
        "order_id": md.source_column("order_id", data_type="int64"),
        "kind": md.source_column("kind", data_type="string"),
        "occurred_at": md.source_column("occurred_at", data_type="timestamp")}),
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
count_key = ms.measure_column(name="count_key", entity=events, column="event_id", additivity="additive")
event_count = ms.aggregate(name="event_count", measure=count_key, agg="count")
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


def _journey(session: mv.Session) -> mv.LogicalEventDataset:
    created = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.created"), name="order"),
        key="created",
    )
    paid = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.paid"), name="order"), key="paid"
    )
    return session.events.match(
        mv.sequence(created, paid),
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
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("relationship resolution attempted source I/O")

    monkeypatch.setattr(md, "connect", forbidden)
    session = mv.session.get_or_create("relationship", report_timezone="UTC")
    metric = session.observe(ms.ref.metric("sales.event_count")).with_dimensions(
        ms.ref.dimension("sales.orders.region")
    )
    assert isinstance(metric, mv.LogicalMetricDataset)
    assert isinstance(_journey(session), mv.LogicalEventDataset)
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
        session.observe(ms.ref.metric("sales.event_count"))
        .with_dimensions(ms.ref.dimension("sales.orders.region"))
        .aggregate()
        .execute()
        .to_pandas()
    )
    assert dict(zip(metric["region"], metric["event_count"], strict=True)) == {"east": 2, "west": 1}
    rows = _journey(session).execute().to_pandas()
    assert len(rows) == 4
    assert rows["step_key"].tolist().count("created") == 2
    assert rows["step_key"].tolist().count("paid") == 2
