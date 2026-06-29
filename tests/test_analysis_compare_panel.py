from __future__ import annotations

import json
from datetime import UTC, datetime

import ibis
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import AlignmentFailedError, PanelGrainMismatchError
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from marivo.analysis.lineage import Lineage
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from marivo.analysis.session._runtime import persist_frame
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
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
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
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
        timescope={"start": start, "end": end},
        grain=grain,
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
        session=session,
    )


def test_window_bucket_aligns_equal_length_panel_by_ordinal_bucket(tmp_path):
    s = _session(tmp_path)
    cur = _panel(s, start="2026-07-01", end="2026-07-03")
    prev = _panel(s, start="2026-06-24", end="2026-06-26")

    delta = compare(cur, prev, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

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

    delta = compare(cur, prev, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

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
    cur = _panel(s, start="2026-07-01", end="2026-07-02")
    prev = _panel(s, start="2026-06-24", end="2026-06-24")

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            cur,
            prev,
            alignment=AlignmentPolicy(kind="window_bucket", strict_lengths=True),
            session=s,
        )

    assert "equal expected bucket counts" in str(exc_info.value)
    assert exc_info.value.details["kind"] == "WindowBucketExpectedCountMismatch"


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


def _now():
    return datetime(2026, 5, 29, 10, 0, 0, tzinfo=UTC)


def _component_panel_metric(session, *, ref, rows, component_rows):
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        },
        "region": {"role": "dimension", "column": "region"},
    }
    metric = make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.failure_rate",
        axes=axes,
        measure={"name": "failure_rate"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )
    metric.meta = metric.meta.model_copy(
        update={
            "ref": ref,
            "composition": {
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            },
        }
    )
    metric.meta = persist_frame(session, metric)
    component = ComponentFrame(
        _df=pd.DataFrame(component_rows),
        meta=ComponentFrameMeta(
            ref=f"{ref}_components",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=len(component_rows),
            byte_size=0,
            lineage=Lineage(),
            parent_ref=metric.ref,
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            composition_kind="ratio",
            components={
                "numerator": "sales.failed_count",
                "denominator": "sales.total_count",
            },
            axes=axes,
            semantic_kind="panel",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)
    metric.meta = metric.meta.model_copy(update={"component_ref": component.ref})
    metric.meta = persist_frame(session, metric)
    return metric


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

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

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
    assert df.iloc[11]["pct_change"] == float("inf")
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

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = out.to_pandas()
    row = df[df["bucket_start"].astype(str) == "2026-07-02"].iloc[0]
    assert pd.isna(row["presence_status"])
    assert pd.isna(row["current"])
    assert pd.isna(row["baseline"])
    assert pd.isna(row["delta"])
    assert pd.isna(row["pct_change"])
    assert row["pct_change_status"] == "not_computable"


def _write_calendar(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True)
    (calendar_dir / "cn_holidays.json").write_text(
        json.dumps(
            {
                "name": "cn_holidays",
                "holidays": [],
                "adjusted_workdays": [],
            }
        ),
        encoding="utf-8",
    )


def test_compare_panel_window_bucket(tmp_path):
    s = _session(tmp_path)
    current = _panel(s, start="2026-07-01", end="2026-07-04")
    baseline = _panel(s, start="2026-07-01", end="2026-07-04")

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

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
        alignment=AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
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
    assert by_bucket["2026-07-03"].pct_change == float("inf")
    assert by_bucket["2026-07-03"].pct_change_status == "from_zero_growth"
    assert out.meta.alignment["mode"] == "calendar_bucket"


def test_compare_panel_calendar_alignment_one_sided_segment_has_consistent_columns(tmp_path):
    _write_calendar(tmp_path)
    s = session_attach.get_or_create(name="demo")
    current = _panel_metric(
        s,
        [
            {"bucket_start": "2026-05-05", "region": "APP", "value": 10.0},
            {"bucket_start": "2026-05-05", "region": "WEB", "value": 100.0},
        ],
    )
    baseline = _panel_metric(
        s,
        [
            {"bucket_start": "2026-04-07", "region": "API", "value": 60.0},
            {"bucket_start": "2026-04-07", "region": "WEB", "value": 80.0},
        ],
    )

    out = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=s,
    )

    df = out.to_pandas()
    assert list(df.columns) == [
        "region",
        "presence_status",
        "align_key",
        "align_quality",
        "bucket_start_a",
        "bucket_start_b",
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
    ]
    by_region = {row.region: row for row in df.itertuples()}
    assert by_region["APP"].presence_status == "new"
    assert by_region["APP"].align_quality == "unmatched"
    assert by_region["APP"].bucket_start_a == "2026-05-05"
    assert pd.isna(by_region["APP"].bucket_start_b)
    assert by_region["APP"].baseline == pytest.approx(0.0)
    assert by_region["APP"].delta == pytest.approx(10.0)
    assert by_region["API"].presence_status == "churned"
    assert by_region["API"].align_quality == "unmatched"
    assert pd.isna(by_region["API"].bucket_start_a)
    assert by_region["API"].bucket_start_b == "2026-04-07"
    assert by_region["API"].current == pytest.approx(0.0)
    assert by_region["API"].delta == pytest.approx(-60.0)
    assert by_region["WEB"].align_quality == "exact"
    assert by_region["WEB"].presence_status == "matched"
    assert by_region["WEB"].bucket_start_a == "2026-05-05"
    assert by_region["WEB"].bucket_start_b == "2026-04-07"


def test_compare_panel_grain_mismatch(tmp_path):
    s = _session(tmp_path)
    current = _panel(s, start="2026-07-01", end="2026-07-03", grain="day")
    baseline = _panel(s, start="2026-06-01", end="2026-08-01", grain="month")

    with pytest.raises(PanelGrainMismatchError):
        compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)


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
        compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)


def test_compare_calendar_panel_ratio_persists_component_delta(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True)
    (calendar_dir / "cn_holidays.json").write_text(
        json.dumps(
            {
                "name": "cn_holidays",
                "holidays": [],
                "adjusted_workdays": [],
            }
        ),
        encoding="utf-8",
    )
    s = session_attach.get_or_create(name="demo")
    current = _component_panel_metric(
        s,
        ref="frame_current_panel_ratio",
        rows=[
            {"bucket_start": "2026-05-05", "region": "WEB", "failure_rate": 0.25},
            {"bucket_start": "2026-05-05", "region": "APP", "failure_rate": 0.50},
        ],
        component_rows=[
            {
                "bucket_start": "2026-05-05",
                "region": "WEB",
                "failed_count": 25.0,
                "total_count": 100.0,
                "failure_rate": 0.25,
            },
            {
                "bucket_start": "2026-05-05",
                "region": "APP",
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
    )
    baseline = _component_panel_metric(
        s,
        ref="frame_baseline_panel_ratio",
        rows=[
            {"bucket_start": "2026-04-07", "region": "WEB", "failure_rate": 0.10},
        ],
        component_rows=[
            {
                "bucket_start": "2026-04-07",
                "region": "WEB",
                "failed_count": 10.0,
                "total_count": 100.0,
                "failure_rate": 0.10,
            },
        ],
    )

    out = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=s,
    )

    component_df = out.components().to_pandas()
    assert {"region", "align_key", "align_quality", "bucket_start_a", "bucket_start_b"}.issubset(
        component_df.columns
    )
    by_region = component_df.set_index("region")
    assert by_region.loc["WEB", "align_quality"] == "exact"
    assert by_region.loc["WEB", "current_failed_count"] == pytest.approx(25.0)
    assert by_region.loc["WEB", "baseline_failed_count"] == pytest.approx(10.0)
    assert by_region.loc["APP", "align_quality"] == "unmatched"
    assert by_region.loc["APP", "baseline_failed_count"] == pytest.approx(0.0)
    assert by_region.loc["APP", "delta_failed_count"] == pytest.approx(50.0)
