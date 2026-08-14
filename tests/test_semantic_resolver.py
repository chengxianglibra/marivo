from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta

import ibis
import pytest

import marivo.semantic as ms
from marivo._compat import UTC
from marivo._temporal import (
    GregorianIsoResolver,
    TemporalResolver,
    TemporalSetSnapshotStore,
    TimeScopeContractV1,
    WorkScheduleSnapshotStore,
    certify_period_calendar,
    certify_period_calendar_rows,
    certify_temporal_set,
    certify_temporal_set_rows,
    certify_work_schedule,
    certify_work_schedule_rows,
    time_scope,
)
from marivo.refs import ref
from marivo.semantic.catalog import SemanticCatalog, SemanticKind
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import StrptimeParse
from tests.ref_helpers import make_ref


class _FakeConnections:
    def __init__(self, backend):
        self.backend = backend
        self.names: list[str] = []

    def session_backend(self, name: str):
        self.names.append(name)
        return self.backend

    @contextmanager
    def use_backend(self, name: str):
        self.names.append(name)
        yield self.backend

    def close_all(self) -> None:
        pass


def _catalog(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def amount(table):\n"
                "    return table.amount\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def total_amount(table):\n"
                "    return table.amount.sum()\n"
            ),
        }
    )
    return SemanticCatalog(project)


def test_resolver_table_uses_connection_service(semantic_project_factory):
    backend = ibis.duckdb.connect(":memory:")
    backend.con.execute("CREATE TABLE orders (amount DOUBLE)")
    connections = _FakeConnections(backend)
    resolver = _catalog(semantic_project_factory)._semantic_resolver(connections=connections)

    table = resolver.table(ms.ref.entity("sales.orders"))

    assert "amount" in table.columns
    assert connections.names == ["warehouse"]


def test_resolver_dimension_on_accepts_semantic_ref(semantic_project_factory):
    resolver = _catalog(semantic_project_factory)._semantic_resolver(
        connections=_FakeConnections(None)
    )
    table = ibis.table({"amount": "float64"}, name="supplied_orders")

    value = resolver.dimension_on(
        make_ref("sales.orders.amount", SemanticKind.DIMENSION),
        table,
    )

    assert isinstance(value, ibis.expr.types.Value)


def test_resolver_metric_on_rejects_wrong_kind(semantic_project_factory):
    resolver = _catalog(semantic_project_factory)._semantic_resolver(
        connections=_FakeConnections(None)
    )
    table = ibis.table({"amount": "float64"}, name="supplied_orders")

    with pytest.raises(SemanticRuntimeError) as exc_info:
        resolver.metric_on(make_ref("sales.orders.amount", SemanticKind.DIMENSION), table)

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "expected metric" in str(exc_info.value)


def _period_rows() -> list[dict[str, object]]:
    return [
        {"date": date(2026, 1, 1), "week": "FY26-W01", "month": "FY26-P01"},
        {"date": date(2026, 1, 2), "week": "FY26-W01", "month": "FY26-P01"},
        {"date": date(2026, 1, 3), "week": "FY26-W02", "month": "FY26-P01"},
        {"date": date(2026, 1, 4), "week": "FY26-W02", "month": "FY26-P01"},
    ]


def test_period_snapshot_is_order_independent_and_resolves_exact_scopes() -> None:
    kwargs = {
        "calendar_ref": ref.period_calendar("sales.fiscal"),
        "boundary_timezone": "UTC",
        "coverage": (date(2026, 1, 1), date(2026, 1, 5)),
        "levels": {"week": "week", "month": "month"},
    }
    first = certify_period_calendar(rows=_period_rows(), **kwargs)
    second = certify_period_calendar(rows=reversed(_period_rows()), **kwargs)

    assert first.snapshot_digest == second.snapshot_digest
    resolver = TemporalResolver(first)
    assert resolver.period_on("week", date(2026, 1, 3)).key == "FY26-W02"
    assert resolver.rolls_up_to("week", "month") is True
    scope = resolver.scope("week", "FY26-W01")
    assert (scope.start, scope.end, scope.kind) == (
        date(2026, 1, 1),
        date(2026, 1, 3),
        "calendar_period",
    )
    assert resolver.period_before("week", date(2026, 1, 4)).key == "FY26-W01"


def test_period_snapshot_rejects_missing_date_and_discontiguous_period_key() -> None:
    kwargs = {
        "calendar_ref": ref.period_calendar("sales.fiscal"),
        "boundary_timezone": "UTC",
        "coverage": (date(2026, 1, 1), date(2026, 1, 5)),
        "levels": {"week": "week"},
    }
    with pytest.raises(ValueError, match="first missing date"):
        certify_period_calendar(rows=_period_rows()[:-1], **kwargs)
    rows = _period_rows()
    rows[1]["week"] = "FY26-W02"
    rows[2]["week"] = "FY26-W01"
    with pytest.raises(ValueError, match="discontiguous"):
        certify_period_calendar(rows=rows, **kwargs)


def test_period_snapshot_certifies_one_persisted_snapshot_value_set() -> None:
    snapshot = certify_period_calendar_rows(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 1, 5)),
        columns=("calendar_date", "fiscal_week"),
        retained_values=(
            ("2026-01-01", "W1"),
            ("2026-01-02", "W1"),
            ("2026-01-03", "W2"),
            ("2026-01-04", "W2"),
        ),
        date_column="calendar_date",
        levels={"week": "fiscal_week"},
    )
    assert snapshot.period_scope("week", "W2").start == date(2026, 1, 3)


def test_period_snapshot_certifies_functional_named_correspondence() -> None:
    rows = [
        {"date": date(2026, 1, 1), "week": "W1", "baseline": None},
        {"date": date(2026, 1, 2), "week": "W1", "baseline": None},
        {"date": date(2026, 1, 3), "week": "W2", "baseline": "W1"},
        {"date": date(2026, 1, 4), "week": "W2", "baseline": "W1"},
    ]
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 1, 5)),
        rows=rows,
        levels={"week": "week"},
        correspondences={"prior": ("week", "baseline")},
    )

    assert TemporalResolver(snapshot).correspondence("prior", "week", "W2") == "W1"
    rows[-1]["baseline"] = "W2"
    with pytest.raises(ValueError, match="conflicting baseline"):
        certify_period_calendar(
            calendar_ref=ref.period_calendar("sales.fiscal"),
            boundary_timezone="UTC",
            coverage=(date(2026, 1, 1), date(2026, 1, 5)),
            rows=rows,
            levels={"week": "week"},
            correspondences={"prior": ("week", "baseline")},
        )


def test_period_snapshot_rejects_mixed_null_and_non_null_correspondence() -> None:
    rows = [
        {"date": date(2026, 1, 1), "week": "W1", "baseline": None},
        {"date": date(2026, 1, 2), "week": "W1", "baseline": "W2"},
        {"date": date(2026, 1, 3), "week": "W2", "baseline": None},
        {"date": date(2026, 1, 4), "week": "W2", "baseline": None},
    ]
    with pytest.raises(ValueError, match="conflicting baseline"):
        certify_period_calendar(
            calendar_ref=ref.period_calendar("sales.fiscal"),
            boundary_timezone="UTC",
            coverage=(date(2026, 1, 1), date(2026, 1, 5)),
            rows=rows,
            levels={"week": "week"},
            correspondences={"prior": ("week", "baseline")},
        )


def test_gregorian_iso_resolver_uses_half_open_period_before_without_epsilon() -> None:
    resolver = GregorianIsoResolver()
    week = resolver.period_on("week", date(2026, 1, 1))
    assert (week.start_date, week.end_date, week.key) == (
        date(2025, 12, 29),
        date(2026, 1, 5),
        "2026-W01",
    )
    previous = resolver.period_before("month", date(2026, 2, 1))
    assert (previous.start_date, previous.end_date, previous.key) == (
        date(2026, 1, 1),
        date(2026, 2, 1),
        "2026-01",
    )


def test_gregorian_iso_resolver_matches_stdlib_across_wide_range() -> None:
    resolver = GregorianIsoResolver()
    dates = [
        date(year, month, day)
        for year in range(1995, 2036)
        for month, day in ((1, 1), (2, 28), (3, 1), (6, 30), (12, 31))
    ]
    for value in dates:
        iso = value.isocalendar()
        week = resolver.period_on("week", value)
        assert week.key == f"{iso.year}-W{iso.week:02d}"
        assert week.start_date == value - timedelta(days=value.weekday())
        month = resolver.period_on("month", value)
        assert month.key == f"{value.year}-{value.month:02d}"
        quarter = resolver.period_on("quarter", value)
        assert quarter.key == f"{value.year}-Q{((value.month - 1) // 3) + 1}"
        year = resolver.period_on("year", value)
        assert year.key == str(value.year)


def test_53_week_fixture_requires_explicit_shifted_and_unshifted_correspondence() -> None:
    start = date(2026, 1, 1)
    end = start + timedelta(days=53 * 7)
    rows = []
    for offset in range((end - start).days):
        week_number = offset // 7 + 1
        rows.append(
            {
                "date": start + timedelta(days=offset),
                "week": f"W{week_number:02d}",
                "shifted": None if week_number == 1 else f"W{week_number - 1:02d}",
                "unshifted": None if week_number == 53 else f"W{week_number + 1:02d}",
            }
        )

    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.retail"),
        boundary_timezone="UTC",
        coverage=(start, end),
        rows=rows,
        levels={"week": "week"},
        correspondences={
            "shifted": ("week", "shifted"),
            "unshifted": ("week", "unshifted"),
        },
    )
    resolver = TemporalResolver(snapshot)
    assert len(tuple(period for period in snapshot.periods if period.level_name == "week")) == 53
    assert resolver.correspondence("shifted", "week", "W52") == "W51"
    assert resolver.correspondence("unshifted", "week", "W52") == "W53"
    assert resolver.correspondence("shifted", "week", "W01") is None
    assert resolver.correspondence("unshifted", "week", "W53") is None


def test_period_snapshot_keeps_day_derived_without_persisting_daily_records() -> None:
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 1, 5)),
        rows=_period_rows(),
        levels={"week": "week"},
    )

    assert "day" in snapshot.levels
    assert all(period.level_name != "day" for period in snapshot.periods)
    day = TemporalResolver(snapshot).period_on("day", date(2026, 1, 3))
    assert (day.key, day.start_date, day.end_date, day.global_ordinal) == (
        "2026-01-03",
        date(2026, 1, 3),
        date(2026, 1, 4),
        2,
    )
    assert snapshot.period_scope("day", "2026-01-03").kind == "calendar_period"


def test_period_snapshot_distinguishes_json_scalar_key_types() -> None:
    rows = [
        {"date": date(2026, 1, 1), "key": 1},
        {"date": date(2026, 1, 2), "key": True},
    ]
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 1, 3)),
        rows=rows,
        levels={"bucket": "key"},
    )

    resolver = TemporalResolver(snapshot)
    assert resolver.period("bucket", 1).key == 1
    assert resolver.period("bucket", True).key is True


def test_temporal_set_snapshot_is_order_independent_and_preserves_exact_occurrence_scope() -> None:
    kwargs = {
        "temporal_set_ref": ref.temporal_set("sales.campaigns"),
        "boundary_timezone": "Asia/Shanghai",
        "coverage": (date(2026, 1, 1), date(2027, 1, 1)),
        "occurrence_id": "campaign_id",
        "start": "starts_on",
        "end": "ends_on",
        "category": "category",
    }
    rows = [
        {
            "campaign_id": "spring",
            "starts_on": date(2026, 3, 1),
            "ends_on": date(2026, 3, 4),
            "category": "promotion",
        },
        {
            "campaign_id": "holiday",
            "starts_on": date(2026, 1, 20),
            "ends_on": date(2026, 1, 22),
            "category": None,
        },
        # Gaps and overlaps are valid for a named occurrence set.
        {
            "campaign_id": "overlap",
            "starts_on": date(2026, 3, 3),
            "ends_on": date(2026, 3, 5),
            "category": "incident",
        },
    ]
    first = certify_temporal_set(rows=rows, **kwargs)
    second = certify_temporal_set(rows=reversed(rows), **kwargs)

    assert first.snapshot_digest == second.snapshot_digest
    scope = first.occurrence_scope("spring")
    assert scope.kind == "temporal_occurrence"
    assert scope.start == date(2026, 3, 1)
    assert scope.end == date(2026, 3, 4)
    assert scope.contract().model_dump() == {
        "schema": "time-scope/v1",
        "kind": "temporal_occurrence",
        "start": date(2026, 3, 1),
        "end": date(2026, 3, 4),
        "temporal_set_ref": "sales.campaigns",
        "snapshot_digest": first.snapshot_digest,
        "boundary_timezone": "Asia/Shanghai",
        "key": "spring",
        "occurrence_category": "promotion",
    }
    assert first.occurrence_scope("holiday").contract().model_dump()["occurrence_category"] is None


def test_temporal_set_snapshot_rows_reject_invalid_encoding_bounds_and_category() -> None:
    common = {
        "temporal_set_ref": ref.temporal_set("sales.campaigns"),
        "boundary_timezone": "UTC",
        "coverage": (date(2026, 1, 1), date(2026, 2, 1)),
        "columns": ("id", "start", "end", "category"),
        "occurrence_id": "id",
        "start": "start",
        "end": "end",
        "category": "category",
    }
    with pytest.raises(ValueError, match="duplicate"):
        certify_temporal_set_rows(
            retained_values=(
                ("same", "2026-01-02", "2026-01-03", "holiday"),
                ("same", "2026-01-04", "2026-01-05", "holiday"),
            ),
            **common,
        )
    with pytest.raises(ValueError, match="mix date and timestamp"):
        certify_temporal_set_rows(
            retained_values=(
                ("date", "2026-01-02", "2026-01-03", None),
                ("instant", "2026-01-04T00:00:00Z", "2026-01-05T00:00:00Z", None),
            ),
            **common,
        )
    with pytest.raises(ValueError, match="start < end"):
        certify_temporal_set_rows(
            retained_values=(("empty", "2026-01-04", "2026-01-04", None),),
            **common,
        )
    with pytest.raises(ValueError, match="category"):
        certify_temporal_set_rows(
            retained_values=(("bad-category", "2026-01-04", "2026-01-05", 1),),
            **common,
        )


def test_temporal_set_timestamp_rows_normalize_to_one_instant_encoding() -> None:
    snapshot = certify_temporal_set_rows(
        temporal_set_ref=ref.temporal_set("sales.campaigns"),
        boundary_timezone="Asia/Shanghai",
        coverage=(date(2026, 1, 1), date(2026, 1, 3)),
        columns=("id", "start", "end"),
        retained_values=(
            ("launch", "2026-01-01T08:00:00+08:00", "2026-01-02T00:00:00+08:00"),
            ("incident", "2026-01-02T00:00:00Z", "2026-01-02T08:00:00Z"),
        ),
        occurrence_id="id",
        start="start",
        end="end",
    )

    assert snapshot.encoding == "timestamp"
    assert snapshot.occurrences[0].start.tzinfo is not None
    assert snapshot.occurrences[0].start == datetime(2026, 1, 1, 0, tzinfo=UTC)
    assert snapshot.occurrence_scope("launch").boundary_timezone == "Asia/Shanghai"


def test_temporal_set_rows_reapply_time_dimension_parse_convention() -> None:
    parse = StrptimeParse(format="%Y%m%d")
    snapshot = certify_temporal_set_rows(
        temporal_set_ref=ref.temporal_set("sales.campaigns"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 1, 4)),
        columns=("id", "start", "end"),
        retained_values=(("launch", "20260101", "20260103"),),
        occurrence_id="id",
        start="start",
        end="end",
        start_parse=parse,
        end_parse=parse,
    )

    assert snapshot.encoding == "date"
    assert snapshot.occurrences[0].start == date(2026, 1, 1)
    assert snapshot.occurrences[0].end == date(2026, 1, 3)


def test_work_schedule_snapshot_is_order_independent_and_requires_complete_boolean_days() -> None:
    kwargs = {
        "work_schedule_ref": ref.work_schedule("sales.cn_schedule"),
        "boundary_timezone": "Asia/Shanghai",
        "coverage": (date(2026, 1, 1), date(2026, 1, 5)),
        "date_column": "date",
        "is_working": "is_working",
    }
    rows = [
        {"date": date(2026, 1, 1), "is_working": False},
        {"date": date(2026, 1, 2), "is_working": True},
        # A makeup Saturday is an authored fact, not a derived weekday rule.
        {"date": date(2026, 1, 3), "is_working": True},
        {"date": date(2026, 1, 4), "is_working": False},
    ]
    first = certify_work_schedule(rows=rows, **kwargs)
    second = certify_work_schedule(rows=reversed(rows), **kwargs)

    assert first.snapshot_digest == second.snapshot_digest
    assert first.working_dates == (date(2026, 1, 2), date(2026, 1, 3))
    assert first.status_on(date(2026, 1, 3)) is True
    with pytest.raises(ValueError, match="duplicate"):
        certify_work_schedule(rows=[*rows, rows[0]], **kwargs)
    with pytest.raises(ValueError, match="non-null booleans"):
        certify_work_schedule(
            rows=[
                {**row, "is_working": None} if index == 1 else row for index, row in enumerate(rows)
            ],
            **kwargs,
        )


def test_work_schedule_rows_reject_timestamp_dates_and_store_exact_history(tmp_path) -> None:
    common = {
        "work_schedule_ref": ref.work_schedule("sales.cn_schedule"),
        "boundary_timezone": "UTC",
        "coverage": (date(2026, 1, 1), date(2026, 1, 3)),
        "columns": ("date", "is_working"),
        "date_column": "date",
        "is_working": "is_working",
    }
    with pytest.raises(ValueError, match="civil dates"):
        certify_work_schedule_rows(
            retained_values=(("2026-01-01T00:00:00Z", True), ("2026-01-02", False)),
            **common,
        )

    first = certify_work_schedule(
        work_schedule_ref=common["work_schedule_ref"],
        boundary_timezone="UTC",
        coverage=common["coverage"],
        rows=[
            {"date": date(2026, 1, 1), "is_working": True},
            {"date": date(2026, 1, 2), "is_working": False},
        ],
        date_column="date",
        is_working="is_working",
    )
    second = certify_work_schedule(
        work_schedule_ref=common["work_schedule_ref"],
        boundary_timezone="UTC",
        coverage=common["coverage"],
        rows=[
            {"date": date(2026, 1, 1), "is_working": False},
            {"date": date(2026, 1, 2), "is_working": True},
        ],
        date_column="date",
        is_working="is_working",
    )
    store = WorkScheduleSnapshotStore(tmp_path)
    store.publish(first, definition_digest="definition-1")
    store.publish(second, definition_digest="definition-2")
    status, current = store.inspect_current(
        common["work_schedule_ref"], definition_digest="definition-2"
    )
    assert status == "current"
    assert current == second
    assert (
        store.load_exact(common["work_schedule_ref"], snapshot_digest=first.snapshot_digest)
        == first
    )


def test_temporal_set_loader_rejects_mixed_start_end_time_encodings(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Data', default=True)\n"
            ),
            "sales/campaigns.py": """
import marivo.datasource as md
import marivo.semantic as ms

campaigns = ms.entity(
    name="campaigns",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("campaigns"),
)
occurrence_id = ms.dimension_column(name="occurrence_id", entity=campaigns, column="id")
start = ms.time_dimension_column(
    name="start", entity=campaigns, column="start", granularity="day",
    parse=ms.strptime("%Y%m%d"),
)
end = ms.time_dimension_column(
    name="end", entity=campaigns, column="end", granularity="day",
    parse=ms.datetime(timezone="UTC"),
)
ms.temporal_set(
    name="campaigns", occurrence_id=occurrence_id, start=start, end=end,
    boundary_timezone="UTC",
    coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2027, 1, 1)),
)
""",
        }
    )

    assert any("same civil-date or timestamp encoding" in str(error) for error in project.errors())


def test_temporal_set_loader_rejects_unresolved_category_ref(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/campaigns.py": """
import marivo.datasource as md
import marivo.semantic as ms

campaigns = ms.entity(
    name="campaigns",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("campaigns"),
)
occurrence_id = ms.dimension_column(name="occurrence_id", entity=campaigns, column="id")
start = ms.time_dimension_column(name="start", entity=campaigns, column="start", granularity="day")
end = ms.time_dimension_column(name="end", entity=campaigns, column="end", granularity="day")
ms.temporal_set(
    name="campaigns", occurrence_id=occurrence_id, start=start, end=end,
    category=ms.ref.dimension("sales.campaigns.missing_category"),
    boundary_timezone="UTC",
    coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2027, 1, 1)),
)
""",
        }
    )

    errors = project.errors()
    assert any(
        error.kind == ErrorKind.INVALID_REF
        and "category" in str(error)
        and "must be fields on one source entity" in str(error)
        for error in errors
    )


def test_temporal_set_snapshot_store_retains_exact_history(tmp_path) -> None:
    temporal_set_ref = ref.temporal_set("sales.campaigns")
    first = certify_temporal_set(
        temporal_set_ref=temporal_set_ref,
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 2, 1)),
        rows=[
            {"id": "first", "start": date(2026, 1, 2), "end": date(2026, 1, 3)},
        ],
        occurrence_id="id",
        start="start",
        end="end",
    )
    second = certify_temporal_set(
        temporal_set_ref=temporal_set_ref,
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 2, 1)),
        rows=[
            {"id": "second", "start": date(2026, 1, 4), "end": date(2026, 1, 5)},
        ],
        occurrence_id="id",
        start="start",
        end="end",
    )
    store = TemporalSetSnapshotStore(tmp_path)
    store.publish(first, definition_digest="definition-1")
    store.publish(second, definition_digest="definition-2")

    status, current = store.inspect_current(temporal_set_ref, definition_digest="definition-2")
    assert status == "current"
    assert current == second
    assert store.load_exact(temporal_set_ref, snapshot_digest=first.snapshot_digest) == first


def test_time_scope_contract_preserves_normalized_bounds_and_provenance() -> None:
    absolute = time_scope(start="2026-01-01", end="2026-02-01")
    assert isinstance(absolute.contract(), TimeScopeContractV1)
    assert absolute.contract().model_dump() == {
        "schema": "time-scope/v1",
        "kind": "absolute",
        "start": date(2026, 1, 1),
        "end": date(2026, 2, 1),
    }
