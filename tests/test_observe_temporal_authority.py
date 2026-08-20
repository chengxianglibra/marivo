"""Frame temporal authority records the per-axis timezone authority (issue #103).

The observe temporal contract historically carried only a single
``display_timezone`` (the report timezone).  The planner now records the
executor-resolved physical, declared, or datasource-read authority instead of
reconstructing it after execution.  These tests lock that contract.
"""

from __future__ import annotations

from datetime import date

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo._temporal import FrameTemporalContractV1, TimeAxisTimeZoneV1


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


def _bootstrap_timestamp_project(tmp_path, *, parse: str = "") -> None:
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "\n"
        f"@ms.time_dimension(entity=orders, granularity='day'{parse})\n"
        "def created_at(orders):\n"
        "    return orders.created_at\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_naive_timestamp_orders(con) -> None:
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at TIMESTAMP, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, TIMESTAMP '2026-07-01 08:00:00', 10.0),"
        "(2, TIMESTAMP '2026-07-02 09:00:00', 20.0)"
    )


def _seed_aware_timestamp_orders(con) -> None:
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at TIMESTAMPTZ, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, TIMESTAMPTZ '2026-07-01 08:00:00+00:00', 10.0),"
        "(2, TIMESTAMPTZ '2026-07-02 09:00:00+00:00', 20.0)"
    )


def _bootstrap_date_project(tmp_path) -> None:
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "from marivo.semantic.ir import DateParse\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "\n"
        "order_date = ms.time_dimension_column("
        "name='order_date', entity=orders, column='created_at', granularity='day', "
        "parse=DateParse())\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_date_orders(con) -> None:
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES (1, DATE '2026-07-01', 10.0),(2, DATE '2026-07-02', 20.0)"
    )


def _bootstrap_native_date_project(tmp_path) -> None:
    """A project whose only time axis is a native DATE column with ``parse`` omitted."""
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def test_frame_temporal_contract_records_time_axis_timezones() -> None:
    contract = FrameTemporalContractV1(
        display_timezone="Asia/Shanghai",
        time_axis_timezones=(
            TimeAxisTimeZoneV1(
                time_dimension="sales.orders.created_at",
                timezone="UTC",
                source="datasource_read",
            ),
        ),
    )
    payload = contract.model_dump(mode="json")
    assert payload["display_timezone"] == "Asia/Shanghai"
    assert payload["time_axis_timezones"] == [
        {
            "time_dimension": "sales.orders.created_at",
            "timezone": "UTC",
            "source": "datasource_read",
        }
    ]


def test_frame_temporal_contract_defaults_to_no_time_axes() -> None:
    contract = FrameTemporalContractV1(display_timezone="UTC")
    assert contract.time_axis_timezones == ()


def test_default_observe_records_datasource_read_timezone_on_temporal_authority(tmp_path) -> None:
    """End-to-end: default observe over a naive timestamp axis records the read tz.

    A time dimension with no ``parse``/timezone leaves the datasource read
    timezone as the effective interpretation; the frame temporal authority must
    surface that assumption even when no explicit ``time_dimension`` was passed
    (issue #103 P2-b).
    """
    from marivo.analysis.intents.observe import observe
    from marivo.semantic.catalog import SemanticKind
    from tests.ref_helpers import make_ref

    _bootstrap_timestamp_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_naive_timestamp_orders(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
        session=session,
    )

    contract = frame.meta.temporal_contract
    assert contract is not None
    assert contract.data_extent_end == date(2026, 7, 2)
    axes = contract.time_axis_timezones
    assert [item.time_dimension for item in axes] == ["sales.orders.created_at"]
    assert [item.source for item in axes] == ["datasource_read"]
    assert all(item.timezone for item in axes)


def test_default_observe_records_physical_timezone_from_aware_axis(tmp_path) -> None:
    """The physical dtype, not datasource read settings, owns an aware axis."""
    from marivo.analysis.intents.observe import observe
    from marivo.semantic.catalog import SemanticKind
    from tests.ref_helpers import make_ref

    _bootstrap_timestamp_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("SET TimeZone='Asia/Shanghai'")
    _seed_aware_timestamp_orders(con)
    session = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
        report_timezone="UTC",
    )

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
        session=session,
    )

    expected = (
        TimeAxisTimeZoneV1(
            time_dimension="sales.orders.created_at",
            timezone="UTC",
            source="physical",
        ),
    )
    assert frame.meta.temporal_contract is not None
    assert frame.meta.temporal_contract.time_axis_timezones == expected
    recovered = session.get_frame(frame.ref)
    assert recovered.meta.temporal_contract is not None
    assert recovered.meta.temporal_contract.data_extent_end == date(2026, 7, 2)
    assert recovered.meta.temporal_contract.time_axis_timezones == expected


def test_default_observe_records_declared_timezone_from_naive_axis(tmp_path) -> None:
    """A parse timezone remains the authority for a naive physical timestamp."""
    from marivo.analysis.intents.observe import observe
    from marivo.semantic.catalog import SemanticKind
    from tests.ref_helpers import make_ref

    _bootstrap_timestamp_project(tmp_path, parse=', parse=ms.timestamp(timezone="UTC")')
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("SET TimeZone='Asia/Shanghai'")
    _seed_naive_timestamp_orders(con)
    session = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
        report_timezone="Asia/Shanghai",
    )

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
        session=session,
    )

    assert frame.meta.temporal_contract is not None
    assert frame.meta.temporal_contract.time_axis_timezones == (
        TimeAxisTimeZoneV1(
            time_dimension="sales.orders.created_at",
            timezone="UTC",
            source="declared",
        ),
    )


def test_default_observe_does_not_record_date_axis_timezone(tmp_path) -> None:
    """End-to-end: a DATE axis adopts no timezone, so no entry is recorded.

    DATE columns are bucketed day-level from the raw column, skipped by the
    readiness naive-timezone check, and window bounds compare raw dates — the
    frame temporal authority must NOT record a datasource-read timezone for a
    DATE axis (issue #103 follow-up P2).
    """
    from marivo.analysis.intents.observe import observe
    from marivo.semantic.catalog import SemanticKind
    from tests.ref_helpers import make_ref

    _bootstrap_date_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_date_orders(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
        session=session,
    )

    contract = frame.meta.temporal_contract
    assert contract is not None
    assert contract.time_axis_timezones == ()


def test_default_observe_does_not_record_native_date_axis_timezone(tmp_path) -> None:
    """End-to-end: an omitted-parse native DATE axis records no timezone.

    ``parse`` is omitted (the recommended form for native temporal columns), so
    the catalog ``data_type`` is ``None`` and the executor infers ``DateParse``
    from the physical column at analysis time. The resolver must align with that
    inference and skip the axis rather than record a datasource-read timezone.
    """
    from marivo.analysis.intents.observe import observe
    from marivo.semantic.catalog import SemanticKind
    from tests.ref_helpers import make_ref

    _bootstrap_native_date_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_date_orders(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
        session=session,
    )

    contract = frame.meta.temporal_contract
    assert contract is not None
    assert contract.time_axis_timezones == ()
