"""Downstream consumers of expression Entities through the shared boundary.

Covers Phase 4 of the entity-expression design: ordinary observation, metric
filters, cumulative calculations, relationship keys, snapshot/validity
versioning, Event journeys, StateModel lifecycle replay, and representative
backend compilation all consume the transformed Entity output relation and
its grain. Fixtures seed superseded revisions that only the Entity body can
remove, so bypassing the body flips the asserted business answers. Structured
load failures cover removed Event-identity and subject-key output fields.
Direct-entity consumer behavior is not re-tested here; these tests establish
that the shared resolver boundary holds for expression declarations too.
"""

from __future__ import annotations

from typing import Any, cast

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.refs import DimensionKind, MetricKind, Ref, TimeDimensionKind
from tests.ref_helpers import make_ref


def _metric(path: str) -> Ref[MetricKind]:
    return cast("Ref[MetricKind]", make_ref(path, ms.SemanticKind.METRIC))


def _dimension(path: str) -> Ref[DimensionKind]:
    return cast("Ref[DimensionKind]", make_ref(path, ms.SemanticKind.DIMENSION))


def _time_dimension(path: str) -> Ref[TimeDimensionKind]:
    return cast("Ref[TimeDimensionKind]", make_ref(path, ms.SemanticKind.TIME_DIMENSION))


# ---------------------------------------------------------------------------
# Shared warehouse seed: revision rows where the latest revision may change
# region, so a pre-body filter over the raw region would give a wrong answer.
# ---------------------------------------------------------------------------


def _orders_connection() -> ibis.backends.duckdb.Backend:
    connection = ibis.duckdb.connect(":memory:")
    connection.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER, revision_id INTEGER, region VARCHAR, "
        "amount DOUBLE, updated_at TIMESTAMP)"
    )
    connection.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, 1, 'east', 10.0, TIMESTAMP '2026-07-01 00:00:00'), "
        "(1, 2, 'west', 12.0, TIMESTAMP '2026-07-02 00:00:00'), "
        "(2, 1, 'east', 20.0, TIMESTAMP '2026-07-01 00:00:00'), "
        "(3, 1, 'west', 30.0, TIMESTAMP '2026-07-02 00:00:00'), "
        "(4, 1, 'east', 40.0, TIMESTAMP '2026-07-03 00:00:00'), "
        "(5, 1, 'north', 50.0, TIMESTAMP '2026-07-02 00:00:00'), "
        "(5, 2, 'north', 55.0, TIMESTAMP '2026-07-05 00:00:00')"
    )
    return connection


# One latest revision per order: region moves with the latest revision.
_LATEST_ORDERS_MODEL = """\
import ibis

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"))
def latest_orders(raw):
    \"\"\"One latest revision per order; region reflects the final state.\"\"\"
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["order_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    )


order_id = ms.dimension_column(name="order_id", entity=latest_orders, column="order_id")
region = ms.dimension_column(name="region", entity=latest_orders, column="region")
amount = ms.measure_column(
    name="amount", entity=latest_orders, column="amount", additivity="additive", unit="USD"
)
updated_at = ms.time_dimension_column(
    name="updated_at", entity=latest_orders, column="updated_at",
    granularity="day", is_default=True,
)
order_revenue = ms.aggregate(name="order_revenue", measure=amount, agg="sum")
west_revenue = ms.aggregate(
    name="west_revenue", measure=amount, agg="sum", filter=ms.where(region="west")
)
order_count = ms.count(name="order_count", entity=latest_orders)
cumulative_revenue = ms.cumulative(name="cumulative_revenue", base=order_revenue, over=updated_at)
"""

# Grouped entity: revenue and order rows per region; output grain is regions.
_REGIONAL_SALES_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"))
def regional_sales(raw):
    \"\"\"Order amounts and row counts per region.\"\"\"
    return raw.group_by("region").aggregate(
        sales_amount=raw["amount"].sum(),
        order_count=raw.count(),
    )


region = ms.dimension_column(name="region", entity=regional_sales, column="region")
sales_amount = ms.measure_column(
    name="sales_amount", entity=regional_sales, column="sales_amount",
    additivity="additive", unit="USD",
)
regional_revenue = ms.aggregate(name="regional_revenue", measure=sales_amount, agg="sum")
regional_orders = ms.count(name="regional_orders", entity=regional_sales)
"""

# Entity joined by relationship on an output key. The event log's latest
# revision per event is the transformed grain the join must agree with.
_LIFECYCLE_MODEL = """\
import ibis

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"), primary_key=["order_id"])
def latest_orders(raw):
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["order_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    )


@ms.entity(datasource=wh, source=md.table("event_log"))
def final_events(raw):
    \"\"\"One latest revision per event; status reflects the final state.\"\"\"
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["event_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    )


order_id = ms.dimension_column(name="order_id", entity=latest_orders, column="order_id")
region = ms.dimension_column(name="region", entity=latest_orders, column="region")
amount = ms.measure_column(
    name="amount", entity=latest_orders, column="amount", additivity="additive", unit="USD"
)
event_id = ms.dimension_column(name="event_id", entity=final_events, column="event_id")
event_order_id = ms.dimension_column(
    name="order_id", entity=final_events, column="order_id"
)
event_status = ms.dimension_column(name="status", entity=final_events, column="status")
event_time = ms.time_dimension_column(
    name="event_time", entity=final_events, column="event_time",
    granularity="second", parse=ms.timestamp(timezone="UTC"), is_default=True,
)
order_to_event = ms.relationship(
    name="order_to_event", from_entity=final_events, to_entity=latest_orders,
    keys=[ms.join_on(event_order_id, order_id)],
)


@ms.metric(
    entities=[final_events, latest_orders],
    root_entity=final_events,
    additivity="additive",
    name="events_by_order_region",
)
def events_by_order_region(final_events, latest_orders):
    return final_events.event_id.count()


@ms.event(
    name="order_shipped", identity=(event_id,), occurred_at=event_time,
    participants=(ms.participant(name="order", path=(order_to_event,), cardinality="one"),),
)
def order_shipped(rows):
    return ms.bind(event_status, rows) == "shipped"


@ms.event(
    name="order_cancelled", identity=(event_id,), occurred_at=event_time,
    participants=(ms.participant(name="order", path=(order_to_event,), cardinality="one"),),
)
def order_cancelled(rows):
    return ms.bind(event_status, rows) == "cancelled"

shipped_state = ms.lifecycle_state(name="shipped", initial=True)
cancelled_state = ms.lifecycle_state(name="cancelled", terminal=True)
order_lifecycle = ms.state_model(
    name="order_lifecycle",
    subject=latest_orders,
    states=(shipped_state, cancelled_state),
    transitions=(
        ms.inception(on=ms.ref.event("sales.order_shipped")),
        ms.transition(
            from_state=shipped_state,
            on=ms.ref.event("sales.order_cancelled"),
            to_state=cancelled_state,
        ),
    ),
)
"""


# ---------------------------------------------------------------------------
# Observation, grouping, Metric filters, and cumulative over output fields
# ---------------------------------------------------------------------------


def test_observe_groups_and_filters_by_output_derived_region(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Observation segments and metric filters read the transformed region."""
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    project_files = _project_files(_LATEST_ORDERS_MODEL)
    _write_project(tmp_path, project_files)
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _orders_connection()
    session = session_attach.get_or_create(
        name="expr-observe", backends={"warehouse": lambda: connection}
    )

    # Grouped observation over the output-derived region dimension.
    grouped = session.observe(
        _metric("sales.order_revenue"),
        dimensions=[_dimension("sales.latest_orders.region")],
    )
    grouped_rows = grouped.to_pandas().set_index("region")["order_revenue"].to_dict()
    # Order 1 moved east -> west at its latest revision, so west revenue
    # includes it; a raw-region grouping would put it in east. Order 5's
    # latest revision belongs to north.
    assert grouped_rows == {"east": 60.0, "west": 42.0, "north": 55.0}

    # Metric filter over the output-derived region.
    filtered = session.observe(_metric("sales.west_revenue"))
    assert filtered.to_pandas().to_dict(orient="records") == [{"west_revenue": 42.0}]

    # Entity count counts output rows: one per order, not seven raw revisions.
    counted = session.observe(_metric("sales.order_count"))
    assert counted.to_pandas().to_dict(orient="records") == [{"order_count": 5}]


def test_cumulative_accumulates_output_grain_rows(tmp_path: Any, monkeypatch: Any) -> None:
    """Cumulative revenue accumulates one row per order, not per revision."""
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_LATEST_ORDERS_MODEL))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _orders_connection()
    session = session_attach.get_or_create(
        name="expr-cumulative", backends={"warehouse": lambda: connection}
    )

    frame = session.observe(
        _metric("sales.cumulative_revenue"),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
    )
    exported = frame.to_pandas()
    buckets = exported["bucket_start"].astype("datetime64[s]").dt.date.astype(str)
    by_day = dict(zip(buckets, exported["cumulative_revenue"].astype(float), strict=True))
    # Latest-revision updated_at per order: order 1 -> 07-02 (12.0),
    # order 2 -> 07-01 (20.0), order 3 -> 07-02 (30.0), order 4 -> 07-03
    # (40.0), order 5 -> 07-05 (55.0, outside the requested window).
    # Cumulative revenue grows by each order's latest revision only;
    # intermediate revisions never double-count.
    assert by_day == {
        "2026-07-01": pytest.approx(20.0),
        "2026-07-02": pytest.approx(62.0),
        "2026-07-03": pytest.approx(102.0),
    }


def test_grouped_entity_observation_reads_output_schema(tmp_path: Any, monkeypatch: Any) -> None:
    """A grouped expression Entity observes through its new schema and grain."""
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_REGIONAL_SALES_MODEL))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _orders_connection()
    session = session_attach.get_or_create(
        name="expr-grouped", backends={"warehouse": lambda: connection}
    )

    frame = session.observe(
        _metric("sales.regional_revenue"),
        dimensions=[_dimension("sales.regional_sales.region")],
    )
    rows = frame.to_pandas().set_index("region")["regional_revenue"].to_dict()
    assert rows == {"east": 70.0, "west": 42.0, "north": 105.0}

    counted = session.observe(_metric("sales.regional_orders"))
    # Entity rows are regional aggregates: counting them counts regions, and
    # the per-region sums agree with the underlying orders.
    assert counted.to_pandas().to_dict(orient="records") == [{"regional_orders": 3}]


# ---------------------------------------------------------------------------
# Relationships and row identity against the transformed grain
# ---------------------------------------------------------------------------


def test_relationship_join_and_event_use_output_grain(tmp_path: Any, monkeypatch: Any) -> None:
    """Relationship keys and Event subjects consume transformed output rows."""
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_LIFECYCLE_MODEL))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _lifecycle_connection()
    session = session_attach.get_or_create(
        name="expr-lifecycle", backends={"warehouse": lambda: connection}
    )

    # Relationship join through both expression Entities: the cross-dataset
    # metric roots at final output event rows and joins each to its latest
    # output order row (one-to-one at the transformed grain). Grouping by the
    # related entity's output region exercises keys from both outputs.
    frame = session.observe(
        _metric("sales.events_by_order_region"),
        dimensions=[_dimension("sales.latest_orders.region")],
    )
    rows = frame.to_pandas().set_index("region")["events_by_order_region"].to_dict()
    # Latest event revision per event: e1 shipped (order 1 -> west at latest
    # revision), e2 shipped (order 2 -> east), e3 cancelled (order 3 -> west).
    assert rows == {"east": 1, "west": 2}

    # Event subject identity resolves through the transformed event rows and
    # the relationship to transformed order rows. Only latest revisions with
    # status='shipped' are occurrences: only e1 (07-02). e2's superseded
    # 'shipped' revision (07-01) was replaced by 'reverted' (07-04), and
    # e3's latest revision is 'cancelled', so neither is an occurrence.
    bounds = session.events.occurrence_bounds(ms.ref.event("sales.order_shipped"))
    assert bounds.earliest_occurrence_at is not None
    assert bounds.latest_occurrence_at is not None
    assert str(bounds.earliest_occurrence_at.date()) == "2026-07-02"
    assert str(bounds.latest_occurrence_at.date()) == "2026-07-02"


def test_event_journey_subjects_resolve_through_output_grain(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Journey subjects and funnel counts ride the transformed event grain."""
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_LIFECYCLE_MODEL))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _lifecycle_connection()
    session = session_attach.get_or_create(
        name="expr-journey", backends={"warehouse": lambda: connection}
    )

    # Superseded revisions must not appear as occurrences: e1 has a
    # 'created' revision at 07-01, but its latest output revision is
    # 'shipped' at 07-02; e2's 'shipped' revision at 07-01 is superseded by
    # 'reverted' at 07-04; e3's latest is 'cancelled' at 07-03. Bypassing
    # the Entity body would resurrect e2's shipped occurrence and seed a
    # second subject.
    shipped = ms.participant_role(event=ms.ref.event("sales.order_shipped"), name="order")
    cancelled = ms.participant_role(event=ms.ref.event("sales.order_cancelled"), name="order")
    journeys = session.events.match(
        pattern=mv.sequence(mv.step(participant=shipped, key="ship")),
        cohort_window=mv.time_scope(start="2026-07-01T00:00:00Z", end="2026-07-04T00:00:00Z"),
        completion_through="2026-07-10T00:00:00Z",
        matching=mv.first_per_subject(),
    )
    rows = journeys.to_pandas()
    # One shipped subject (order 1) joined through output order rows; order
    # 2's shipped revision was reverted in its latest revision.
    subjects = sorted(rows["subject_identity"].tolist())
    assert subjects == [(1,)]
    step_keys = rows.groupby("subject_identity")["step_key"].apply(list).tolist()
    assert all(keys == ["ship"] for keys in step_keys)

    funnel = session.events.funnel(journeys)
    funnel_rows = funnel.to_pandas()
    assert funnel_rows["cohort_count"].tolist() == [1]
    assert funnel_rows["reached_count"].tolist() == [1]
    # A follow-up cancellation step would find only order 3's event, which is
    # not a subject of this cohort: journeys and funnels agree on output rows.
    cancel_journeys = session.events.match(
        pattern=mv.sequence(mv.step(participant=cancelled, key="cancel")),
        cohort_window=mv.time_scope(start="2026-07-01T00:00:00Z", end="2026-07-04T00:00:00Z"),
        completion_through="2026-07-10T00:00:00Z",
        matching=mv.first_per_subject(),
    )
    cancel_subjects = sorted(cancel_journeys.to_pandas()["subject_identity"].tolist())
    assert cancel_subjects == [(3,)]


def test_lifecycle_replay_consumes_output_grain(tmp_path: Any, monkeypatch: Any) -> None:
    """State-model replay rides expression-Entity outputs on both sides.

    Occurrences come from final_events output rows; the subject identities
    come from latest_orders output rows joined through the relationship.
    """
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_LIFECYCLE_MODEL))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _lifecycle_connection()
    session = session_attach.get_or_create(
        name="expr-lifecycle-replay", backends={"warehouse": lambda: connection}
    )

    history = session.lifecycle.replay(
        ms.ref.state_model("sales.order_lifecycle"),
        window=mv.time_scope(start="2026-07-01T00:00:00Z", end="2026-07-31T00:00:00Z"),
        seed=mv.from_inception(),
    )
    rows = history.to_pandas()
    # Latest event revisions: e1 shipped 07-02 (order 1), e2 reverted 07-04
    # (order 2 — its earlier 'shipped' revision is superseded), e3 cancelled
    # 07-03 (order 3, never shipped so unmodeled). Superseded revisions are
    # not occurrences and cannot seed state. Without a completeness
    # declaration the surviving shipped interval is coverage_censored at the
    # window end. Bypassing the body would resurrect e2's shipped revision
    # and seed order 2 as well.
    observed = list(
        zip(
            rows["subject_identity"],
            rows["model_state"],
            rows["interval_status"],
            strict=True,
        )
    )
    assert observed == [
        ((1,), "shipped", "coverage_censored"),
    ]

    # Declaring complete coverage promotes order 3's provable no-inception
    # from coverage censoring into a hard replay failure: its only output
    # event is 'cancelled', which never seeds the shipped initial state.
    from marivo.analysis.errors import InsufficientStateHistoryError

    with pytest.raises(InsufficientStateHistoryError):
        session.lifecycle.replay(
            ms.ref.state_model("sales.order_lifecycle"),
            window=mv.time_scope(start="2026-07-01T00:00:00Z", end="2026-07-31T00:00:00Z"),
            seed=mv.from_inception(),
            completeness=(
                mv.declared_complete_through(
                    inputs=(
                        ms.ref.event("sales.order_shipped"),
                        ms.ref.event("sales.order_cancelled"),
                    ),
                    through="2026-07-31T00:00:00Z",
                    rationale="Fixture is fully reconciled through the window end.",
                ),
            ),
        )


def _identity_load_failure(project: Any) -> Any:
    """Load a project whose expression Entity drops identity-critical fields.

    Returns the first structured SemanticError; the load must be failed.
    """
    result = project.load()
    assert not project.is_ready(), "identity-dropping model must fail to load"
    errors = project.errors()
    assert errors, "expected structured load errors"
    return errors[0]


# Projected-source variants of the lifecycle model so static output validation
# has declared input metadata and identity failures surface at load. The
# orders projection omits nothing the positive tests need; the dropped models
# then re-group the dedup windows so an identity-critical output column
# disappears and static validation must reject the downstream references.
_LIFECYCLE_PROJECTED_MODEL = _LIFECYCLE_MODEL.replace(
    '@ms.entity(datasource=wh, source=md.table("event_log"))',
    "@ms.entity(\n"
    "    datasource=wh,\n"
    "    source=md.table(\n"
    '        "event_log",\n'
    "        columns={\n"
    '            "event_id": md.source_column("event_id", data_type="string"),\n'
    '            "order_id": md.source_column("order_id", data_type="int64"),\n'
    '            "status": md.source_column("status", data_type="string"),\n'
    '            "revision_id": md.source_column("revision_id", data_type="int64"),\n'
    '            "event_time": md.source_column("event_time", data_type="timestamp"),\n'
    "        },\n"
    "    ),\n"
    ")",
).replace(
    '@ms.entity(datasource=wh, source=md.table("orders"), primary_key=["order_id"])',
    "@ms.entity(\n"
    "    datasource=wh,\n"
    "    source=md.table(\n"
    '        "orders",\n'
    "        columns={\n"
    '            "order_id": md.source_column("order_id", data_type="int64"),\n'
    '            "revision_id": md.source_column("revision_id", data_type="int64"),\n'
    '            "region": md.source_column("region", data_type="string"),\n'
    '            "amount": md.source_column("amount", data_type="float64"),\n'
    '            "updated_at": md.source_column("updated_at", data_type="timestamp"),\n'
    "        },\n"
    "    ),\n"
    '    primary_key=["order_id"],\n'
    ")",
)

_EVENT_IDENTITY_DROPPED_MODEL = _LIFECYCLE_PROJECTED_MODEL.replace(
    """        == 0
    )


order_id = ms.dimension_column""",
    """        == 0
    ).select("order_id", "status", "event_time")


order_id = ms.dimension_column""",
)

_SUBJECT_KEY_DROPPED_MODEL = _LIFECYCLE_PROJECTED_MODEL.replace(
    """def latest_orders(raw):
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["order_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    )""",
    """def latest_orders(raw):
    return raw.select("region", "amount", "updated_at")
""",
)


def test_event_identity_dropped_from_output_fails_with_repair(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Removing the Event identity field fails load with structured output repair."""
    from marivo.semantic.reader import SemanticProject

    _write_project(tmp_path, _project_files(_EVENT_IDENTITY_DROPPED_MODEL))
    error = _identity_load_failure(SemanticProject(workspace_dir=tmp_path))
    assert error.details["available_output_columns"] == [
        "order_id",
        "status",
        "event_time",
    ]
    dropped = error.details["missing_references"]
    assert {item["received_column"] for item in dropped} == {"event_id"}


def test_subject_identity_dropped_from_output_fails_with_repair(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Removing the subject identity key fails load with structured output repair."""
    from marivo.semantic.reader import SemanticProject

    _write_project(tmp_path, _project_files(_SUBJECT_KEY_DROPPED_MODEL))
    error = _identity_load_failure(SemanticProject(workspace_dir=tmp_path))
    assert error.details["available_output_columns"] == [
        "region",
        "amount",
        "updated_at",
    ]
    dropped = error.details["missing_references"]
    assert {item["received_column"] for item in dropped} == {"order_id"}


def _lifecycle_connection() -> ibis.backends.duckdb.Backend:
    connection = _orders_connection()
    connection.raw_sql(
        "CREATE TABLE event_log ("
        "event_id VARCHAR, order_id INTEGER, status VARCHAR, revision_id INTEGER, "
        "event_time TIMESTAMP)"
    )
    connection.raw_sql(
        "INSERT INTO event_log VALUES "
        "('e1', 1, 'created', 1, TIMESTAMP '2026-07-01 00:00:00'), "
        "('e1', 1, 'shipped', 2, TIMESTAMP '2026-07-02 00:00:00'), "
        "('e2', 2, 'shipped', 1, TIMESTAMP '2026-07-01 00:00:00'), "
        "('e2', 2, 'reverted', 2, TIMESTAMP '2026-07-04 00:00:00'), "
        "('e3', 3, 'created', 1, TIMESTAMP '2026-07-02 00:00:00'), "
        "('e3', 3, 'cancelled', 2, TIMESTAMP '2026-07-03 00:00:00')"
    )
    return connection


# ---------------------------------------------------------------------------
# Project-file plumbing shared by the session-based tests above
# ---------------------------------------------------------------------------


def _project_files(model: str, *, domain: str = "sales") -> dict[str, str]:
    return {
        "datasources/warehouse.py": (
            "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
        ),
        f"{domain}/_domain.py": (
            "import marivo.semantic as ms\n"
            f"ms.domain(name={domain!r}, owner='Mina Zhang', default=True)\n"
        ),
        f"{domain}/entities.py": model,
    }


# ---------------------------------------------------------------------------
# Snapshot/validity versioning on output fields
# ---------------------------------------------------------------------------

# Daily snapshots where the expression body deduplicates to one latest
# snapshot row per (snapshot_date, product_id); versioning axes are output
# fields that also survive the body.
_SNAPSHOT_MODEL = """\
import ibis

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")

snapshot_ref = ms.ref.entity("inventory.latest_snapshots")
snapshot_date = ms.time_dimension_column(
    name='snapshot_date', entity=snapshot_ref, column='snapshot_date',
    granularity='day', is_default=True,
)


@ms.entity(
    datasource=wh, source=md.table("snapshots"),
    primary_key=["snapshot_date", "product_id"],
    versioning=ms.snapshot(partition_field=snapshot_date, grain='day'),
)
def latest_snapshots(raw):
    \"\"\"One latest snapshot record per snapshot date and product.\"\"\"
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["snapshot_date"], raw["product_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    )


product_id = ms.dimension_column(
    name='product_id', entity=snapshot_ref, column='product_id',
)
category = ms.dimension_column(
    name='category', entity=snapshot_ref, column='category',
)


@ms.measure(
    entity=snapshot_ref,
    additivity=ms.semi_additive(over=snapshot_date, fold='last'),
    unit='item',
)
def quantity_on_hand(latest_snapshots):
    return latest_snapshots.quantity_on_hand


end_inventory = ms.aggregate(
    name='end_inventory', measure=quantity_on_hand, agg='sum', fold='last',
)
"""

# The same shape, but the body drops the versioning partition axis. The
# Source is unprojected, so static output validation defers to runtime; the
# failure must surface at first use with the missing output axis named.
_SNAPSHOT_DROPPED_AXIS_MODEL = _SNAPSHOT_MODEL.replace(
    """    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["snapshot_date"], raw["product_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    )""",
    """    return raw.group_by("product_id").aggregate(
        quantity_on_hand=raw["quantity_on_hand"].last(),
    )""",
)

_VALIDITY_MODEL = """\
import ibis

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")

history_ref = ms.ref.entity("sales.latest_history")
valid_from = ms.dimension_column(
    name='valid_from', entity=history_ref, column='valid_from',
)
valid_to = ms.dimension_column(
    name='valid_to', entity=history_ref, column='valid_to',
)


@ms.entity(
    datasource=wh, source=md.table("user_history"),
    primary_key=["user_id", "valid_from"],
    versioning=ms.validity(
        valid_from=valid_from, valid_to=valid_to,
        interval='closed_open', open_end=(None,),
    ),
)
def latest_history(raw):
    \"\"\"Drop superseded history rows, keeping the surviving validity spans.\"\"\"
    return raw.filter(raw["superseded"] == False)


orders = ms.entity(
    name='orders', datasource=wh, source=md.table('orders'),
    primary_key=['order_id'],
)
order_date = ms.time_dimension_column(
    name='order_date', entity=orders, column='created_at',
    granularity='day', is_default=True,
)
order_user_id = ms.dimension_column(
    name='user_id', entity=orders, column='user_id',
)
order_amount = ms.measure_column(
    name='amount', entity=orders, column='amount', additivity='additive', unit='USD',
)
history_user_id = ms.dimension_column(
    name='user_id', entity=history_ref, column='user_id',
)
tier = ms.dimension_column(
    name='tier', entity=history_ref, column='tier',
)
orders_to_history = ms.relationship(
    name='orders_to_history', from_entity=orders, to_entity=history_ref,
    keys=[ms.join_on(order_user_id, history_user_id)],
)


@ms.metric(
    entities=[orders, history_ref],
    root_entity=orders,
    additivity='additive',
    name='revenue_by_tier',
)
def revenue_by_tier(orders, history_ref):
    return orders.amount.sum()
"""


def _snapshot_connection() -> ibis.backends.duckdb.Backend:
    connection = ibis.duckdb.connect(":memory:")
    connection.raw_sql(
        "CREATE TABLE snapshots ("
        "snapshot_date DATE, product_id INTEGER, category VARCHAR, "
        "quantity_on_hand DOUBLE, revision_id INTEGER)"
    )
    connection.raw_sql(
        "INSERT INTO snapshots VALUES "
        "(DATE '2026-01-01', 1, 'A', 10.0, 1), "
        "(DATE '2026-01-02', 1, 'A', 15.0, 1), "
        "(DATE '2026-01-02', 1, 'A', 18.0, 2), "
        "(DATE '2026-01-02', 2, 'B', 20.0, 1)"
    )
    return connection


def _validity_connection() -> ibis.backends.duckdb.Backend:
    connection = ibis.duckdb.connect(":memory:")
    connection.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER)"
    )
    connection.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 100), "
        "(2, DATE '2026-07-05', 20.0, 100)"
    )
    connection.raw_sql(
        "CREATE TABLE user_history ("
        "user_id INTEGER, valid_from DATE, valid_to DATE, tier VARCHAR, superseded BOOLEAN)"
    )
    # The first 'gold' span is a superseded revision the body must drop. It
    # overlaps both orders' dates: bypassing the body matches each order to
    # gold as well as silver, doubling every row into a 'gold' segment.
    connection.raw_sql(
        "INSERT INTO user_history VALUES "
        "(100, DATE '2026-06-01', DATE '2026-08-01', 'gold', TRUE), "
        "(100, DATE '2026-06-15', NULL, 'silver', FALSE)"
    )
    return connection


def test_snapshot_fold_selects_latest_output_rows(tmp_path: Any, monkeypatch: Any) -> None:
    """Snapshot versioning folds over deduplicated output rows only."""
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_SNAPSHOT_MODEL, domain="inventory"))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _snapshot_connection()
    session = session_attach.get_or_create(
        name="expr-snapshot", backends={"warehouse": lambda: connection}
    )

    frame = session.observe(_metric("inventory.end_inventory"))
    # Output rows keep the latest revision per (date, product): (01-01, 1,
    # 10.0), (01-02, 1, 18.0), (01-02, 2, 20.0). The 15.0 revision on the
    # latest date is superseded, so bypassing the body would leave both
    # 15.0 and 18.0 on 01-02 and the last fold would answer 15.0 + 20.0.
    # The correct deduplicated fold picks 18.0 and 20.0.
    assert frame.to_pandas().to_dict(orient="records") == [{"end_inventory": 38.0}]

    segmented = session.observe(
        _metric("inventory.end_inventory"),
        dimensions=[_dimension("inventory.latest_snapshots.category")],
    )
    rows = segmented.to_pandas().set_index("category")["end_inventory"].to_dict()
    assert rows == {"A": 18.0, "B": 20.0}


def test_snapshot_partition_dropped_from_output_fails_at_runtime(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Removing the versioning partition axis from the output fails first use.

    The Source is unprojected, so loading defers output-schema validation;
    the failure must surface at first use naming the missing axis, without
    falling back to the physical source column.
    """
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_SNAPSHOT_DROPPED_AXIS_MODEL, domain="inventory"))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()  # Unprojected Source: schema-dependent checks are deferred.
    connection = _snapshot_connection()
    session = session_attach.get_or_create(
        name="expr-snapshot-dropped",
        backends={"warehouse": lambda: connection},
    )

    with pytest.raises(Exception) as exc_info:
        session.observe(_metric("inventory.end_inventory"))
    message = str(exc_info.value)
    assert "snapshot_date" in message


def test_validity_as_of_uses_surviving_output_spans(tmp_path: Any, monkeypatch: Any) -> None:
    """Validity versioning consumes output spans after the body filters rows."""
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_VALIDITY_MODEL))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _validity_connection()
    session = session_attach.get_or_create(
        name="expr-validity", backends={"warehouse": lambda: connection}
    )

    latest = session.observe(
        _metric("sales.revenue_by_tier"),
        dimensions=[_dimension("sales.latest_history.tier")],
    )
    rows = latest.to_pandas().set_index("tier")["revenue_by_tier"].to_dict()
    # The root time dimension makes this as-of-root-time. Only the
    # open-ended 'silver' span survives the body, so both orders resolve to
    # silver. The superseded 'gold' span (06-01 -> 08-01) overlaps both
    # order dates: bypassing the body would match each order to gold too,
    # producing a spurious {'silver': 30.0, 'gold': 30.0} answer.
    assert rows == {"silver": 30.0}

    as_of = session.observe(
        _metric("sales.revenue_by_tier"),
        dimensions=[_dimension("sales.latest_history.tier")],
        time_dimension=_time_dimension("sales.orders.order_date"),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
    )
    as_of_rows = as_of.to_pandas().set_index("tier")["revenue_by_tier"].to_dict()
    # Clipping the window to 07-01..07-03 keeps only order 1, resolving
    # through the surviving output 'silver' span.
    assert as_of_rows == {"silver": 10.0}


def _write_project(tmp_path: Any, files: dict[str, str], *, domain: str = "sales") -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    root = tmp_path / "models" / "semantic"
    root.mkdir(parents=True, exist_ok=True)
    for rel, source in files.items():
        if rel.startswith("datasources/"):
            full = tmp_path / "models" / rel
        else:
            full = root / rel.replace("sales/", f"{domain}/")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source)


# ---------------------------------------------------------------------------
# Order sensitivity and backend compilation
# ---------------------------------------------------------------------------

# Filter-then-window vs window-then-filter over the same dedup differ in the
# winning row: revision 2 of order 1 is deleted; revision 1 is not. Excluding
# deleted revisions first keeps order 1 alive with amount 10.0; selecting the
# latest revision first removes order 1 entirely.
_ORDER_SENSITIVE_MODEL_A = """\
import ibis

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"), primary_key=["order_id"])
def dedup_then_drop(raw):
    \"\"\"Latest revision per order, then drop deleted revisions.\"\"\"
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["order_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    ).filter(~raw["is_deleted"])


amount = ms.measure_column(
    name="amount", entity=dedup_then_drop, column="amount", additivity="additive", unit="USD"
)
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""

_ORDER_SENSITIVE_MODEL_B = """\
import ibis

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"), primary_key=["order_id"])
def drop_then_dedup(raw):
    \"\"\"Drop deleted revisions first, then take the latest per order.\"\"\"
    return raw.filter(~raw["is_deleted"]).filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["order_id"]],
                order_by=[raw["revision_id"].desc()],
            )
        )
        == 0
    )


amount = ms.measure_column(
    name="amount", entity=drop_then_dedup, column="amount", additivity="additive", unit="USD"
)
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""

# Analysis-filter order sensitivity: an authored Metric filter over an output
# dimension must stay outside the Entity relation. Segmenting the grouped
# output by region equals the raw per-region sums regardless of grouping
# order because aggregation happens on the Entity side.
_REGIONAL_ORDERED_MODEL = _REGIONAL_SALES_MODEL


def _deleted_revision_connection() -> ibis.backends.duckdb.Backend:
    connection = ibis.duckdb.connect(":memory:")
    connection.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER, revision_id INTEGER, amount DOUBLE, is_deleted BOOLEAN)"
    )
    connection.raw_sql(
        "INSERT INTO orders VALUES (1, 1, 10.0, FALSE), (1, 2, 99.0, TRUE), (2, 1, 20.0, FALSE)"
    )
    return connection


def test_metric_filter_order_over_dedup_changes_answer(tmp_path: Any, monkeypatch: Any) -> None:
    """Authored order of dedup vs delete-filter decides the business result."""
    from marivo.semantic.reader import SemanticProject

    connection = _deleted_revision_connection()

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_ORDER_SENSITIVE_MODEL_A))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    session = session_attach.get_or_create(
        name="expr-order-a", backends={"warehouse": lambda: connection}
    )
    # Latest revision of order 1 is deleted, so it leaves the output.
    frame_a = session.observe(_metric("sales.revenue"))
    assert frame_a.to_pandas().to_dict(orient="records") == [{"revenue": 20.0}]

    session_attach._reset_process_state()
    empty_dir = tmp_path / "alt"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)
    _write_project(empty_dir, _project_files(_ORDER_SENSITIVE_MODEL_B))
    project_b = SemanticProject(workspace_dir=empty_dir)
    project_b.load()
    session_b = session_attach.get_or_create(
        name="expr-order-b", backends={"warehouse": lambda: connection}
    )
    # Dropping deleted revisions first keeps order 1's surviving revision.
    frame_b = session_b.observe(_metric("sales.revenue"))
    assert frame_b.to_pandas().to_dict(orient="records") == [{"revenue": 30.0}]


def test_observe_time_scope_stays_outside_entity_relation(tmp_path: Any, monkeypatch: Any) -> None:
    """Time-scope predicates target output fields, not raw input predicates.

    The scope over the output `updated_at` cannot resurrect revisions the
    body removed: totals equal the unscoped output aggregate for groups whose
    surviving output rows fall inside the window.
    """
    from marivo.semantic.reader import SemanticProject

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _write_project(tmp_path, _project_files(_LATEST_ORDERS_MODEL))
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    connection = _orders_connection()
    session = session_attach.get_or_create(
        name="expr-timescope", backends={"warehouse": lambda: connection}
    )

    scoped = session.observe(
        _metric("sales.order_revenue"),
        time_dimension=_time_dimension("sales.latest_orders.updated_at"),
        time_scope=mv.time_scope(start="2026-07-02", end="2026-07-04"),
    )
    value = scoped.to_pandas()["order_revenue"].iloc[0]
    # Output rows: orders 1 (07-02), 2 (07-01), 3 (07-02), 4 (07-03), and
    # order 5 (07-05). The window keeps 12 + 30 + 40 = 82: order 5's latest
    # revision is outside the window, so it leaves the result entirely. A
    # raw-input predicate would keep order 5's 07-02 revision, mis-aggregate
    # its earlier revisions, and answer 132.
    assert value == pytest.approx(82.0)


@pytest.mark.parametrize("dialect", ["duckdb", "trino", "clickhouse"])
def test_entity_body_shapes_compile_on_installed_dialects(dialect: str) -> None:
    """Grouped and window Entity bodies compile on representative backends.

    This is compilation evidence only: it shows Ibis can render the authored
    shapes for each dialect. It is not remote execution, partition-pruning,
    or scan-volume evidence.
    """
    table = ibis.table(
        {
            "order_id": "int64",
            "region": "string",
            "amount": "float64",
            "updated_at": "timestamp",
            "revision_id": "int64",
        },
        name="orders",
    )
    window_dedup = table.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[table["order_id"]],
                order_by=[table["revision_id"].desc()],
            )
        )
        == 0
    )
    grouped = table.group_by("region").aggregate(
        sales_amount=table["amount"].sum(),
        order_count=table.count(),
    )
    for query in (window_dedup, grouped):
        sql = ibis.to_sql(query, dialect=dialect)
        assert "orders" in sql.lower()
