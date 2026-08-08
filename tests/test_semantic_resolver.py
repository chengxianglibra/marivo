from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import ibis
import pytest

import marivo.semantic as ms
from marivo._temporal import (
    GregorianIsoResolver,
    TemporalResolver,
    TimeScope,
    TimeScopeContractV1,
    certify_period_calendar,
    certify_period_calendar_rows,
)
from marivo.refs import ref
from marivo.semantic.catalog import SemanticCatalog, SemanticKind
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
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


def test_time_scope_contract_preserves_normalized_bounds_and_provenance() -> None:
    absolute = TimeScope(start="2026-01-01", end="2026-02-01")
    assert isinstance(absolute.contract(), TimeScopeContractV1)
    assert absolute.contract().model_dump() == {
        "schema": "time-scope/v1",
        "kind": "absolute",
        "start": date(2026, 1, 1),
        "end": date(2026, 2, 1),
    }
