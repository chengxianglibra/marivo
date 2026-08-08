from __future__ import annotations

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    AlignmentFailedError,
    PanelGrainMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from marivo.analysis.policies import window_bucket
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _session_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-06-24', 8.0, 'north', 100),"
        "(2, DATE '2026-06-25', 18.0, 'north', 101),"
        "(3, DATE '2026-06-24', 28.0, 'south', 200),"
        "(4, DATE '2026-06-25', 38.0, 'south', 201),"
        "(5, DATE '2026-07-01', 10.0, 'north', 102),"
        "(6, DATE '2026-07-02', 20.0, 'north', 103),"
        "(7, DATE '2026-07-01', 30.0, 'south', 202),"
        "(8, DATE '2026-07-02', 40.0, 'south', 203)"
    )


def _bootstrap_sales(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _session(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def _panel(session, *, start: str, end: str, grain: str = "day"):
    return observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start=start, end=end),
        grain=mv.grain(grain),
        dimensions=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        session=session,
    )


def test_window_bucket_aligns_equal_length_panel_by_ordinal_bucket(tmp_path):
    s = _session(tmp_path)
    cur = _panel(s, start="2026-07-01", end="2026-07-03")
    prev = _panel(s, start="2026-06-24", end="2026-06-26")

    delta = compare(cur, prev, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert {"bucket_start", "bucket_start_b", "region", "current", "baseline"} <= set(df.columns)
    north = df[df["region"] == "NORTH"].sort_values("bucket_start").reset_index(drop=True)
    assert list(north["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(north["bucket_start_b"].astype(str)) == ["2026-06-24", "2026-06-25"]
    assert list(north["delta"]) == [pytest.approx(2.0), pytest.approx(2.0)]
    assert delta.meta.alignment["mode"] == "ordinal_bucket"


def test_window_bucket_panel_different_expected_counts_uses_outer_ordinal_union(tmp_path):
    s = _session(tmp_path)
    cur = _panel(s, start="2026-07-01", end="2026-07-03")
    prev = _panel(s, start="2026-06-24", end="2026-06-25")

    delta = compare(cur, prev, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    north = df[df["region"] == "NORTH"].sort_values("bucket_start").reset_index(drop=True)
    assert len(north) == 2
    assert list(north["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert str(north.iloc[0]["bucket_start_b"]) == "2026-06-24"
    assert pd.isna(north.iloc[1]["bucket_start_b"])
    assert north.iloc[1]["presence_status"] == "new"
    assert north.iloc[1]["baseline"] == pytest.approx(0.0)
    assert delta.meta.alignment["coverage"]["current_unpaired_buckets"] == 2
    assert delta.meta.alignment["coverage"]["baseline_unpaired_buckets"] == 0


def test_window_bucket_panel_strict_lengths_rejects_different_expected_counts(tmp_path):
    s = _session(tmp_path)
    cur = _panel(s, start="2026-07-01", end="2026-07-03")
    prev = _panel(s, start="2026-06-24", end="2026-06-25")

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            cur,
            prev,
            alignment=window_bucket(strict_lengths=True),
            session=s,
        )

    assert "equal expected bucket counts" in str(exc_info.value)
    assert exc_info.value._context["kind"] == "WindowBucketExpectedCountMismatch"


def _panel_metric(
    session,
    rows,
    *,
    axes: dict[str, object] | None = None,
    window: dict[str, object] | None = None,
):
    return make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes=axes
        or {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            },
            "region": {"role": "dimension", "column": "region"},
        },
        measure={"name": "value"},
        semantic_kind="panel",
        semantic_model="sales",
        window=window,
        session=session,
    )


def test_window_bucket_panel_sparse_segment_uses_window_spine():
    s = session_attach.get_or_create(name="demo")
    current_rows = [
        {
            "bucket_start": f"2026-05-12 {hour:02d}:00:00",
            "region": "WEB",
            "value": float(hour),
        }
        for hour in range(24)
    ]
    baseline_rows = [
        {
            "bucket_start": f"2026-05-05 {hour:02d}:00:00",
            "region": "WEB",
            "value": float(hour + 100),
        }
        for hour in range(11)
    ]
    axes = {
        "time": {"role": "time", "column": "bucket_start", "grain": "hour"},
        "region": {"role": "dimension", "column": "region"},
    }
    current = _panel_metric(
        s,
        current_rows,
        axes=axes,
        window={"start": "2026-05-12", "end": "2026-05-13", "grain": "hour"},
    )
    baseline = _panel_metric(
        s,
        baseline_rows,
        axes=axes,
        window={"start": "2026-05-05", "end": "2026-05-06", "grain": "hour"},
    )

    out = compare(current, baseline, alignment=window_bucket(), session=s)

    df = out.to_pandas()
    assert len(df) == 24
    assert list(df["bucket_start"].astype(str).head(2)) == [
        "2026-05-12 00:00:00",
        "2026-05-12 01:00:00",
    ]
    assert list(df["bucket_start_b"].astype(str).head(2)) == [
        "2026-05-05 00:00:00",
        "2026-05-05 01:00:00",
    ]
    assert df.iloc[10]["baseline"] == pytest.approx(110.0)
    assert df.iloc[11]["presence_status"] == "new"
    assert df.iloc[11]["baseline"] == pytest.approx(0.0)
    assert df.iloc[11]["delta"] == pytest.approx(11.0)
    assert pd.isna(df.iloc[11]["pct_change"])
    assert df.iloc[11]["pct_change_status"] == "from_zero_growth"
    assert out.meta.alignment["coverage"]["baseline"]["missing_buckets"] == 13
    assert out.meta.alignment["segment_info"]["coverage"]["baseline"]["missing_buckets"] == 13


def test_window_bucket_panel_both_missing_spine_row_is_not_new_or_churned():
    s = session_attach.get_or_create(name="demo")
    axes = {
        "time": {"role": "time", "column": "bucket_start", "grain": "day"},
        "region": {"role": "dimension", "column": "region"},
    }
    current = _panel_metric(
        s,
        [{"bucket_start": "2026-07-01", "region": "NORTH", "value": 10.0}],
        axes=axes,
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
    )
    baseline = _panel_metric(
        s,
        [{"bucket_start": "2026-06-24", "region": "NORTH", "value": 5.0}],
        axes=axes,
        window={"start": "2026-06-24", "end": "2026-06-26", "grain": "day"},
    )

    out = compare(current, baseline, alignment=window_bucket(), session=s)

    df = out.to_pandas()
    row = df[df["bucket_start"].astype(str) == "2026-07-02"].iloc[0]
    assert pd.isna(row["presence_status"])
    assert pd.isna(row["current"])
    assert pd.isna(row["baseline"])
    assert pd.isna(row["delta"])
    assert pd.isna(row["pct_change"])
    assert row["pct_change_status"] == "not_computable"


def test_compare_panel_window_bucket(tmp_path):
    s = _session(tmp_path)
    current = _panel(s, start="2026-07-01", end="2026-07-04")
    baseline = _panel(s, start="2026-07-01", end="2026-07-04")

    out = compare(current, baseline, alignment=window_bucket(), session=s)

    assert out.meta.semantic_kind == "panel"
    assert out.meta.alignment["segment_info"]["segment_count"] == 2
    assert out.meta.alignment["segment_info"]["a_only_segments_count"] == 0
    assert out.meta.alignment["segment_info"]["b_only_segments_count"] == 0
    assert out.meta.alignment["segment_info"]["coverage"]["paired_buckets"] == 6
    assert out.meta.alignment["axes"] == current.meta.axes
    df = out.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "bucket_start_b",
        "region",
        "presence_status",
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
    ]
    by_key = {(str(row.bucket_start), row.region): row for row in df.itertuples()}
    assert by_key[("2026-07-01", "NORTH")].delta == pytest.approx(0.0)
    assert by_key[("2026-07-02", "SOUTH")].delta == pytest.approx(0.0)
    assert out.meta.alignment["mode"] == "ordinal_bucket"


def test_compare_panel_window_bucket_calendar_mode_outer_joins_bucket_keys():
    s = session_attach.get_or_create(name="demo")
    current = _panel_metric(
        s,
        [
            {"bucket_start": "2026-07-01", "region": "NORTH", "value": 10.0},
            {"bucket_start": "2026-07-03", "region": "NORTH", "value": 30.0},
        ],
    )
    baseline = _panel_metric(
        s,
        [
            {"bucket_start": "2026-07-01", "region": "NORTH", "value": 8.0},
            {"bucket_start": "2026-07-02", "region": "NORTH", "value": 20.0},
        ],
    )

    out = compare(
        current,
        baseline,
        alignment=window_bucket(mode="calendar_bucket"),
        session=s,
    )

    df = out.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "presence_status",
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
    ]
    by_bucket = {str(row.bucket_start): row for row in df.itertuples()}
    assert by_bucket["2026-07-01"].presence_status == "matched"
    assert by_bucket["2026-07-01"].current == pytest.approx(10.0)
    assert by_bucket["2026-07-01"].baseline == pytest.approx(8.0)
    assert by_bucket["2026-07-01"].delta == pytest.approx(2.0)
    assert by_bucket["2026-07-02"].presence_status == "churned"
    assert by_bucket["2026-07-02"].current == pytest.approx(0.0)
    assert by_bucket["2026-07-02"].baseline == pytest.approx(20.0)
    assert by_bucket["2026-07-02"].delta == pytest.approx(-20.0)
    assert by_bucket["2026-07-02"].pct_change == pytest.approx(-1.0)
    assert by_bucket["2026-07-03"].presence_status == "new"
    assert by_bucket["2026-07-03"].current == pytest.approx(30.0)
    assert by_bucket["2026-07-03"].baseline == pytest.approx(0.0)
    assert by_bucket["2026-07-03"].delta == pytest.approx(30.0)
    assert pd.isna(by_bucket["2026-07-03"].pct_change)
    assert by_bucket["2026-07-03"].pct_change_status == "from_zero_growth"
    assert out.meta.alignment["mode"] == "calendar_bucket"


def test_compare_panel_grain_mismatch(tmp_path):
    s = _session(tmp_path)
    current = _panel(s, start="2026-07-01", end="2026-07-03", grain="day")
    baseline = _panel(s, start="2026-06-01", end="2026-08-01", grain="month")

    with pytest.raises(PanelGrainMismatchError):
        compare(current, baseline, alignment=window_bucket(), session=s)


def test_compare_panel_grain_mismatch_uses_time_axis_role(tmp_path):
    s = session_attach.get_or_create(name="demo")
    current = _panel_metric(
        s,
        [{"bucket_start": "2026-07-01", "region": "NORTH", "value": 10.0}],
        axes={
            "event_time": {"role": "time", "column": "bucket_start", "grain": "day"},
            "region": {"role": "dimension", "column": "region"},
        },
    )
    baseline = _panel_metric(
        s,
        [{"bucket_start": "2026-06-01", "region": "NORTH", "value": 8.0}],
        axes={
            "event_time": {"role": "time", "column": "bucket_start", "grain": "month"},
            "region": {"role": "dimension", "column": "region"},
        },
    )

    with pytest.raises(PanelGrainMismatchError):
        compare(current, baseline, alignment=window_bucket(), session=s)


@pytest.mark.parametrize(
    "conflicting_name",
    [
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
        "presence_status",
    ],
)
def test_compare_panel_rejects_dimension_colliding_with_protocol_column(
    conflicting_name: str,
) -> None:
    """A legal panel dimension named like a compare protocol column must fail
    closed with a typed error instead of a pandas duplicate-column exception
    (issue #39, panel path).
    """
    s = session_attach.get_or_create(name="demo")
    current = _panel_metric(
        s,
        [
            {
                "bucket_start": pd.Timestamp("2026-07-01"),
                conflicting_name: "A",
                "value": 100.0,
            }
        ],
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            },
            conflicting_name: {"role": "dimension", "column": conflicting_name},
        },
    )
    baseline = _panel_metric(
        s,
        [
            {
                "bucket_start": pd.Timestamp("2026-07-01"),
                conflicting_name: "A",
                "value": 70.0,
            }
        ],
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            },
            conflicting_name: {"role": "dimension", "column": conflicting_name},
        },
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(current, baseline, alignment=window_bucket(), session=s)

    error = exc_info.value
    assert error._context["reason"] == "protocol_column_collision"
    assert conflicting_name in error._context["conflicting_columns"]
    assert error.location == "session.compare"
    assert error.repair is not None
    assert error.repair.kind == "semantic_authoring"
    assert [job.intent for job in s.jobs() if job.intent == "compare"] == []


@pytest.mark.parametrize("conflicting_name", ["current", "baseline", "delta"])
def test_compare_panel_rejects_time_column_colliding_with_protocol(
    conflicting_name: str,
) -> None:
    """A panel time column named like a compare protocol column must fail
    closed with a typed error instead of a pandas raw exception (issue #39,
    panel time-column side)."""
    s = session_attach.get_or_create(name="demo")
    cur = _panel_metric(
        s,
        [
            {
                conflicting_name: pd.Timestamp("2026-07-01"),
                "region": "A",
                "value": 100.0,
            }
        ],
        axes={
            "time": {
                "role": "time",
                "column": conflicting_name,
                "grain": "day",
                "time_dimension": "order_date",
            },
            "region": {"role": "dimension", "column": "region"},
        },
    )
    base = _panel_metric(
        s,
        [
            {
                conflicting_name: pd.Timestamp("2026-07-01"),
                "region": "A",
                "value": 70.0,
            }
        ],
        axes={
            "time": {
                "role": "time",
                "column": conflicting_name,
                "grain": "day",
                "time_dimension": "order_date",
            },
            "region": {"role": "dimension", "column": "region"},
        },
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(cur, base, alignment=window_bucket(), session=s)

    error = exc_info.value
    assert error._context["reason"] == "protocol_column_collision"
    assert conflicting_name in error._context["conflicting_columns"]
    assert error.location == "session.compare"
    assert error.repair is not None
    assert error.repair.kind == "semantic_authoring"
