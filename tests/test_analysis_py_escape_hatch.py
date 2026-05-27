from datetime import UTC, datetime

import ibis
import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.frames.exploration import (
    ExplorationResult,
    ExplorationResultMeta,
)
from marivo.analysis_py.lineage import Lineage, LineageStep
from marivo.analysis_py.session.persistence import write_frame_to_disk


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)


def _base_meta(session, *, kind, ref):
    return {
        "kind": kind,
        "ref": ref,
        "session_id": session.id,
        "project_root": str(session.project_root),
        "produced_by_job": None,
        "created_at": _now(),
        "row_count": 1,
        "byte_size": 0,
        "lineage": Lineage(
            steps=[
                LineageStep(
                    intent="from_pandas",
                    job_ref=None,
                    inputs=[],
                    params_digest="scratch",
                )
            ],
            external_inputs=["frame_scratch"],
        ),
    }


def test_exploration_result_round_trips_through_load_frame():
    session = mv.session.create(name="demo")
    scratch = ExplorationResult(
        _df=pd.DataFrame({"country": ["US"], "value": [10.0]}),
        meta=ExplorationResultMeta(
            **_base_meta(session, kind="exploration_result", ref="frame_scratch"),
            source_kind="pandas",
            description="manual check",
            source_query=None,
            source_datasource=None,
            source_artifact_refs=[],
            promotion_refs=[],
        ),
    )
    scratch.meta = write_frame_to_disk(session.layout, scratch)

    loaded = mv.load_frame("frame_scratch", session=session)

    assert isinstance(loaded, ExplorationResult)
    assert loaded.meta.kind == "exploration_result"
    assert loaded.meta.source_kind == "pandas"
    assert loaded.to_pandas().iloc[0]["value"] == 10.0


def test_exploration_result_to_pandas_returns_copy():
    session = mv.session.create(name="demo")
    scratch = ExplorationResult(
        _df=pd.DataFrame({"value": [10.0]}),
        meta=ExplorationResultMeta(
            **_base_meta(session, kind="exploration_result", ref="frame_scratch"),
            source_kind="pandas",
            description=None,
            source_query=None,
            source_datasource=None,
            source_artifact_refs=[],
            promotion_refs=[],
        ),
    )

    df = scratch.to_pandas()
    df.loc[0, "value"] = 99.0

    assert scratch.to_pandas().iloc[0]["value"] == 10.0


def test_from_pandas_creates_persisted_scratch_result():
    session = mv.session.create(name="demo")
    df = pd.DataFrame({"country": ["US", "CA"], "value": [10.0, 5.0]})

    scratch = mv.from_pandas(df, session=session, description="manual cohort scan")

    assert isinstance(scratch, mv.ExplorationResult)
    assert scratch.meta.kind == "exploration_result"
    assert scratch.meta.source_kind == "pandas"
    assert scratch.meta.description == "manual cohort scan"
    assert scratch.meta.source_query is None
    assert scratch.meta.source_datasource is None
    assert scratch.meta.source_artifact_refs == []
    assert scratch.meta.promotion_refs == []
    assert scratch.lineage.steps[-1].intent == "from_pandas"
    assert (session.layout.frames_dir / scratch.ref / "data.parquet").is_file()

    df.loc[0, "value"] = 999.0
    assert scratch.to_pandas().iloc[0]["value"] == 10.0


def test_from_pandas_isolates_object_column_containers():
    session = mv.session.create(name="demo")
    tags = ["baseline"]
    attrs = {"segment": "control"}
    df = pd.DataFrame({"tags": [tags], "attrs": [attrs]})

    scratch = mv.from_pandas(df, session=session)

    tags.append("mutated")
    attrs["segment"] = "variant"

    materialized = scratch.to_pandas()
    assert materialized.iloc[0]["tags"] == ["baseline"]
    assert materialized.iloc[0]["attrs"] == {"segment": "control"}


def test_exploration_result_to_pandas_isolates_object_column_containers():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame(
            {
                "tags": [["baseline"]],
                "attrs": [{"segment": "control"}],
            }
        ),
        session=session,
    )

    materialized = scratch.to_pandas()
    materialized.iloc[0]["tags"].append("mutated")
    materialized.iloc[0]["attrs"]["segment"] = "variant"

    rematerialized = scratch.to_pandas()
    assert rematerialized.iloc[0]["tags"] == ["baseline"]
    assert rematerialized.iloc[0]["attrs"] == {"segment": "control"}


def test_from_pandas_uses_active_session_when_session_is_omitted():
    session = mv.session.create(name="demo")

    scratch = mv.from_pandas(pd.DataFrame({"value": [1.0]}))

    assert scratch.meta.session_id == session.id


def test_explore_ibis_materializes_scratch_and_records_provenance():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (country TEXT, revenue DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES ('US', 10.0), ('CA', 5.0), ('US', 3.0)")
    session = mv.session.create(name="demo", backends={"warehouse": lambda: con})

    def build_us_revenue(backend):
        table = backend.table("orders")
        return (
            table.filter(table.country == "US")
            .group_by(table.country)
            .aggregate(value=table.revenue.sum())
        )

    scratch = mv.explore_ibis(
        build_us_revenue,
        datasource="warehouse",
        session=session,
        description="US revenue scratch",
    )

    assert isinstance(scratch, mv.ExplorationResult)
    assert scratch.meta.source_kind == "ibis"
    assert scratch.meta.source_datasource == "warehouse"
    assert scratch.meta.description == "US revenue scratch"
    assert scratch.meta.source_query is None or "orders" in scratch.meta.source_query.lower()
    assert scratch.to_pandas().iloc[0]["value"] == 13.0
    assert scratch.lineage.steps[-1].intent == "explore_ibis"


def test_explore_ibis_records_source_artifact_refs():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (value DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (1.0)")
    session = mv.session.create(name="demo", backends={"warehouse": lambda: con})

    scratch = mv.explore_ibis(
        lambda backend: backend.table("orders"),
        datasource="warehouse",
        session=session,
        source_artifacts=[mv.ArtifactRef("frame_source")],
    )

    assert scratch.meta.source_artifact_refs == ["frame_source"]
    assert scratch.lineage.steps[-1].inputs == ["frame_source"]


def test_explore_ibis_materializes_scalar_expression_as_value_column():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (value DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (1.0), (2.0), (3.0)")
    session = mv.session.create(name="demo", backends={"warehouse": lambda: con})

    scratch = mv.explore_ibis(
        lambda backend: backend.table("orders").value.sum(),
        datasource="warehouse",
        session=session,
    )

    materialized = scratch.to_pandas()
    assert materialized.shape == (1, 1)
    assert materialized.columns.tolist() == ["value"]
    assert materialized.iloc[0]["value"] == 6.0


def test_explore_ibis_rejects_plain_builder_return():
    con = ibis.duckdb.connect(":memory:")
    session = mv.session.create(name="demo", backends={"warehouse": lambda: con})

    with pytest.raises(TypeError, match=r"Ibis expression|to_pandas|expression"):
        mv.explore_ibis(
            lambda backend: ["not", "an", "expression"],
            datasource="warehouse",
            session=session,
        )


def test_core_operators_reject_exploration_result_inputs():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"value": [1.0]}), session=session)
    metric = mv.promote_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="scalar",
        measure_column="value",
        axes={},
        semantic_model="sales",
    )

    with pytest.raises(mv.errors.SemanticKindMismatchError):
        mv.compare(metric, scratch, session=session)  # type: ignore[arg-type]

    with pytest.raises(mv.errors.SemanticKindMismatchError):
        mv.decompose(scratch, axis=mv.DimensionRef("country"), session=session)  # type: ignore[arg-type]

    with pytest.raises(mv.errors.SemanticKindMismatchError):
        mv.discover(scratch, objective="point_anomalies", session=session)  # type: ignore[arg-type]

    with pytest.raises(mv.errors.SemanticKindMismatchError):
        mv.test(metric, scratch, session=session)  # type: ignore[arg-type]


def test_promote_metric_frame_creates_canonical_metric_frame():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"country": ["US", "CA"], "value": [10.0, 5.0]}),
        session=session,
    )

    metric = mv.promote_metric_frame(
        scratch,
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="segmented",
        measure_column="value",
        axes={"country": mv.DimensionRef("country")},
        semantic_model="sales",
    )

    assert isinstance(metric, mv.MetricFrame)
    assert metric.meta.kind == "metric_frame"
    assert metric.meta.metric_id == "sales.revenue"
    assert metric.meta.semantic_kind == "segmented"
    assert metric.meta.semantic_model == "sales"
    assert metric.meta.measure == {"name": "value"}
    assert metric.meta.axes == {
        "country": {"role": "dimension", "column": "country", "ref": "country"}
    }
    assert metric.lineage.steps[-1].intent == "promote_metric_frame"
    assert scratch.ref in metric.lineage.steps[-1].inputs
    assert (session.layout.frames_dir / metric.ref / "data.parquet").is_file()


def test_promote_metric_frame_accepts_direct_dataframe():
    session = mv.session.create(name="demo")

    metric = mv.promote_metric_frame(
        pd.DataFrame({"value": [42.0]}),
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="scalar",
        measure_column="value",
        axes={},
        semantic_model="sales",
    )

    assert metric.meta.kind == "metric_frame"
    assert metric.to_pandas().iloc[0]["value"] == 42.0
    assert metric.lineage.steps[0].intent == "from_pandas"
    assert metric.lineage.steps[-1].intent == "promote_metric_frame"


def test_promote_metric_frame_fails_closed_with_missing_metadata():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"value": [1.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(scratch, session=session)

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert set(exc_info.value.details["missing"]) >= {
        "metric",
        "semantic_kind",
        "measure_column",
        "semantic_model",
    }
    assert exc_info.value.details["available_columns"] == ["value"]


def test_promote_metric_frame_rejects_non_numeric_measure_column():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"value": ["not numeric"]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="scalar",
            measure_column="value",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["missing"] == []
    assert exc_info.value.details["ambiguous"] == ["non_numeric:value"]
    assert exc_info.value.details["available_columns"] == ["value"]
    assert exc_info.value.details["source_refs"] == [scratch.ref]


def test_promote_metric_frame_rejects_missing_axis_column():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"value": [1.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="segmented",
            measure_column="value",
            axes={"country": mv.DimensionRef("country")},
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["missing"] == ["country"]
    assert exc_info.value.details["available_columns"] == ["value"]
    assert exc_info.value.details["source_refs"] == [scratch.ref]


def test_promote_metric_frame_rejects_segmented_without_axes():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"value": [1.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="segmented",
            measure_column="value",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["missing"] == ["axes"]
    assert exc_info.value.details["available_columns"] == ["value"]


def test_promote_metric_frame_rejects_cross_session_scratch():
    session = mv.session.create(name="demo")
    other_session = mv.session.create(name="other")
    scratch = mv.from_pandas(pd.DataFrame({"value": [1.0]}), session=session)

    with pytest.raises(mv.errors.CrossSessionFrameError):
        mv.promote_metric_frame(
            scratch,
            session=other_session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="scalar",
            measure_column="value",
            semantic_model="sales",
        )


def test_promote_metric_frame_resolves_relative_window_to_absolute():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"bucket_start": ["2026-05-25"], "value": [1.0]}),
        session=session,
    )

    metric = mv.promote_metric_frame(
        scratch,
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="time_series",
        measure_column="value",
        semantic_model="sales",
        time_axis="bucket_start",
        window={"expr": "last 7 days", "as_of": "2026-05-26T12:00:00", "grain": "day"},
    )

    assert metric.meta.axes == {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "ref": "bucket_start",
            "grain": "day",
        }
    }
    assert metric.meta.window == {
        "kind": "absolute",
        "start": "2026-05-20",
        "end": "2026-05-26",
        "grain": "day",
        "tz": None,
        "time_field": None,
    }


def test_promote_metric_frame_rejects_time_series_without_time_axis():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"value": [1.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="time_series",
            measure_column="value",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["missing"] == ["time_axis"]
    assert exc_info.value.details["available_columns"] == ["value"]


def test_promote_metric_frame_rejects_time_series_with_dimension_axes():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"bucket_start": ["2026-05-25"], "country": ["US"], "value": [1.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="time_series",
            measure_column="value",
            axes={"country": mv.DimensionRef("country")},
            time_axis="bucket_start",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["unexpected_axes"]
    assert exc_info.value.details["available_columns"] == [
        "bucket_start",
        "country",
        "value",
    ]


def test_promote_metric_frame_rejects_segmented_with_time_axis():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"bucket_start": ["2026-05-25"], "country": ["US"], "value": [1.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="segmented",
            measure_column="value",
            axes={"country": mv.DimensionRef("country")},
            time_axis="bucket_start",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["unexpected_time_axis"]


def test_promote_metric_frame_rejects_scalar_with_axes():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"country": ["US"], "value": [1.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="scalar",
            measure_column="value",
            axes={"country": mv.DimensionRef("country")},
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["unexpected_axes"]


def test_promote_metric_frame_rejects_measure_column_used_as_axis():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"country": ["US"], "value": [1.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="segmented",
            measure_column="country",
            axes={"country": mv.DimensionRef("country")},
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["measure_axis_collision:country"]


def test_promote_metric_frame_rejects_scalar_with_extra_columns():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"country": ["US"], "value": [1.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="scalar",
            measure_column="value",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["scalar_extra_columns:country"]


def test_promote_metric_frame_rejects_panel_time_axis_collision():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"bucket_start": ["2026-05-25"], "time": ["US"], "value": [1.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="panel",
            measure_column="value",
            axes={"time": mv.DimensionRef("time")},
            time_axis="bucket_start",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["axis_collision:time"]


def test_promote_metric_frame_rejects_panel_time_axis_ref_collision():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"bucket_start": ["2026-05-25"], "country": ["US"], "value": [1.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="panel",
            measure_column="value",
            axes={"country": mv.DimensionRef("time")},
            time_axis="bucket_start",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["axis_collision:time"]


def test_promote_metric_frame_rejects_panel_time_axis_column_key_collision():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"bucket_start": ["2026-05-25"], "value": [1.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="panel",
            measure_column="value",
            axes={"bucket_start": mv.DimensionRef("country")},
            time_axis="bucket_start",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["axis_collision:bucket_start"]


def test_promote_metric_frame_rejects_panel_time_axis_column_ref_collision():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"bucket_start": ["2026-05-25"], "country": ["US"], "value": [1.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_metric_frame(
            scratch,
            session=session,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="panel",
            measure_column="value",
            axes={"country": mv.DimensionRef("bucket_start")},
            time_axis="bucket_start",
            semantic_model="sales",
        )

    assert exc_info.value.details["target_kind"] == "metric_frame"
    assert exc_info.value.details["ambiguous"] == ["axis_collision:bucket_start"]


def _promoted_scalar_metric(session, value, *, semantic_kind="scalar", semantic_model="sales"):
    df = pd.DataFrame({"value": [value]})
    axes = {}
    if semantic_kind == "segmented":
        df = pd.DataFrame({"country": ["US"], "value": [value]})
        axes = {"country": mv.DimensionRef("country")}
    return mv.promote_metric_frame(
        df,
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind=semantic_kind,
        measure_column="value",
        axes=axes,
        semantic_model=semantic_model,
    )


def test_promote_delta_frame_inherits_source_metric_metadata():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0)
    baseline = _promoted_scalar_metric(session, 20.0)
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    delta = mv.promote_delta_frame(
        scratch,
        session=session,
        current=mv.ArtifactRef(current.ref),
        baseline=mv.ArtifactRef(baseline.ref),
        delta_column="delta",
        current_column="current",
        baseline_column="baseline",
    )

    assert isinstance(delta, mv.DeltaFrame)
    assert delta.meta.kind == "delta_frame"
    assert delta.meta.metric_id == "sales.revenue"
    assert delta.meta.semantic_kind == "scalar"
    assert delta.meta.semantic_model == "sales"
    assert delta.meta.source_a_ref == current.ref
    assert delta.meta.source_b_ref == baseline.ref
    assert delta.meta.alignment == {"kind": "calendar_bucket"}
    assert delta.lineage.steps[-1].intent == "promote_delta_frame"
    assert [step.intent for step in delta.lineage.steps].count("promote_metric_frame") == 2
    assert set(current.lineage.external_inputs).issubset(delta.lineage.external_inputs)
    assert set(baseline.lineage.external_inputs).issubset(delta.lineage.external_inputs)
    assert delta.to_pandas().iloc[0]["delta"] == 10.0


def test_promote_delta_frame_fails_when_delta_formula_does_not_match():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0)
    baseline = _promoted_scalar_metric(session, 20.0)
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [99.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "delta_formula" in exc_info.value.details["ambiguous"]


def test_promote_delta_frame_rejects_nullable_formula_values():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0)
    baseline = _promoted_scalar_metric(session, 20.0)
    scratch = mv.from_pandas(
        pd.DataFrame(
            {
                "current": pd.Series([pd.NA], dtype="Int64"),
                "baseline": pd.Series([20], dtype="Int64"),
                "delta": pd.Series([10], dtype="Int64"),
            }
        ),
        session=session,
    )

    with pytest.raises(mv.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "delta_formula_null" in exc_info.value.details["ambiguous"]


def test_promote_delta_frame_rejects_metric_override_mismatch():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0)
    baseline = _promoted_scalar_metric(session, 20.0)
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            metric=mv.MetricRef("sales.profit"),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "metric_override_mismatch" in exc_info.value.details["ambiguous"]


def test_promote_delta_frame_rejects_source_semantic_kind_mismatch():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0, semantic_kind="scalar")
    baseline = _promoted_scalar_metric(session, 20.0, semantic_kind="segmented")
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "semantic_kind_mismatch" in exc_info.value.details["ambiguous"]


def test_promote_delta_frame_rejects_source_semantic_model_mismatch():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0, semantic_model="sales")
    baseline = _promoted_scalar_metric(session, 20.0, semantic_model="finance")
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "semantic_model_mismatch" in exc_info.value.details["ambiguous"]


def test_promote_delta_frame_rejects_source_axes_mismatch():
    session = mv.session.create(name="demo")
    current = mv.promote_metric_frame(
        pd.DataFrame({"country": ["US"], "value": [30.0]}),
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="segmented",
        measure_column="value",
        axes={"country": mv.DimensionRef("country")},
        semantic_model="sales",
    )
    baseline = mv.promote_metric_frame(
        pd.DataFrame({"platform": ["web"], "value": [20.0]}),
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="segmented",
        measure_column="value",
        axes={"platform": mv.DimensionRef("platform")},
        semantic_model="sales",
    )
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "axes_mismatch" in exc_info.value.details["ambiguous"]


def test_promoted_segmented_delta_alignment_includes_axes_for_decompose():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0, semantic_kind="segmented")
    baseline = _promoted_scalar_metric(session, 20.0, semantic_kind="segmented")

    delta = mv.promote_delta_frame(
        pd.DataFrame(
            {
                "country": ["US"],
                "current": [30.0],
                "baseline": [20.0],
                "delta": [10.0],
            }
        ),
        session=session,
        current=mv.ArtifactRef(current.ref),
        baseline=mv.ArtifactRef(baseline.ref),
        delta_column="delta",
        current_column="current",
        baseline_column="baseline",
    )

    assert delta.meta.alignment["axes"] == current.meta.axes
    assert "country" in delta.to_pandas().columns
    attribution = mv.decompose(delta, axis=mv.DimensionRef("country"), session=session)
    assert attribution.meta.driver_field == "country"
    assert attribution.to_pandas().iloc[0]["contribution"] == 10.0


def test_promote_delta_frame_rejects_missing_inherited_axis_column():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0, semantic_kind="segmented")
    baseline = _promoted_scalar_metric(session, 20.0, semantic_kind="segmented")

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert exc_info.value.details["missing"] == ["country"]


def test_promote_delta_frame_rejects_asymmetric_window_grain():
    session = mv.session.create(name="demo")
    current = mv.promote_metric_frame(
        pd.DataFrame({"value": [30.0]}),
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="scalar",
        measure_column="value",
        semantic_model="sales",
        window={"start": "2026-05-01", "end": "2026-05-02", "grain": "day"},
    )
    baseline = mv.promote_metric_frame(
        pd.DataFrame({"value": [20.0]}),
        session=session,
        metric=mv.MetricRef("sales.revenue"),
        semantic_kind="scalar",
        measure_column="value",
        semantic_model="sales",
        window={"start": "2026-05-01", "end": "2026-05-02"},
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "window_grain_mismatch" in exc_info.value.details["ambiguous"]


def test_promote_delta_frame_rejects_semantic_kind_override_mismatch():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0)
    baseline = _promoted_scalar_metric(session, 20.0)
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            semantic_kind="segmented",
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "semantic_kind_override_mismatch" in exc_info.value.details["ambiguous"]


def test_promote_delta_frame_rejects_semantic_model_override_mismatch():
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0)
    baseline = _promoted_scalar_metric(session, 20.0)
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            semantic_model="finance",
            delta_column="delta",
            current_column="current",
            baseline_column="baseline",
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert "semantic_model_override_mismatch" in exc_info.value.details["ambiguous"]


@pytest.mark.parametrize(
    ("current_column", "baseline_column", "missing_column"),
    [
        ("current", None, "baseline_column"),
        (None, "baseline", "current_column"),
    ],
)
def test_promote_delta_frame_rejects_partial_formula_columns(
    current_column,
    baseline_column,
    missing_column,
):
    session = mv.session.create(name="demo")
    current = _promoted_scalar_metric(session, 30.0)
    baseline = _promoted_scalar_metric(session, 20.0)
    scratch = mv.from_pandas(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(
            scratch,
            session=session,
            current=mv.ArtifactRef(current.ref),
            baseline=mv.ArtifactRef(baseline.ref),
            delta_column="delta",
            current_column=current_column,
            baseline_column=baseline_column,
        )

    assert exc_info.value.details["target_kind"] == "delta_frame"
    assert missing_column in exc_info.value.details["missing"]


def test_promote_delta_frame_fails_closed_without_provenance():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(pd.DataFrame({"delta": [10.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_delta_frame(scratch, session=session, delta_column="delta")

    assert set(exc_info.value.details["missing"]) >= {
        "current",
        "baseline",
        "metric",
        "semantic_kind",
        "semantic_model",
    }


def _promoted_delta(session):
    current = _promoted_scalar_metric(session, 30.0, semantic_kind="scalar")
    baseline = _promoted_scalar_metric(session, 20.0, semantic_kind="scalar")
    return mv.promote_delta_frame(
        pd.DataFrame({"current": [30.0], "baseline": [20.0], "delta": [10.0]}),
        session=session,
        current=mv.ArtifactRef(current.ref),
        baseline=mv.ArtifactRef(baseline.ref),
        delta_column="delta",
        current_column="current",
        baseline_column="baseline",
    )


def test_promote_attribution_frame_inherits_source_delta_metadata():
    session = mv.session.create(name="demo")
    delta = _promoted_delta(session)
    scratch = mv.from_pandas(
        pd.DataFrame({"country": ["US", "CA"], "value": [8.0, 2.0], "contribution": [8.0, 2.0]}),
        session=session,
    )

    attribution = mv.promote_attribution_frame(
        scratch,
        session=session,
        source_delta=mv.ArtifactRef(delta.ref),
        driver_field="country",
        value_column="value",
        contribution_column="contribution",
        method="manual",
        params={"note": "scratch attribution"},
    )

    assert isinstance(attribution, mv.AttributionFrame)
    assert attribution.meta.kind == "attribution_frame"
    assert attribution.meta.metric_ids == ["sales.revenue"]
    assert attribution.meta.source_refs == [delta.ref]
    assert attribution.meta.driver_field == "country"
    assert attribution.meta.value_column == "value"
    assert attribution.meta.contribution_column == "contribution"
    assert attribution.meta.semantic_kind == "scalar"
    assert attribution.meta.semantic_model == "sales"
    assert attribution.meta.method == "manual"
    assert attribution.meta.params == {"note": "scratch attribution"}
    assert attribution.lineage.steps[-1].intent == "promote_attribution_frame"
    assert [step.intent for step in attribution.lineage.steps].count("promote_delta_frame") == 1
    assert [step.intent for step in attribution.lineage.steps].count("promote_metric_frame") == 2
    assert set(delta.lineage.external_inputs).issubset(attribution.lineage.external_inputs)


def test_promote_attribution_frame_fails_closed_without_source_delta():
    session = mv.session.create(name="demo")
    scratch = mv.from_pandas(
        pd.DataFrame({"country": ["US"], "contribution": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_attribution_frame(
            scratch,
            session=session,
            driver_field="country",
            contribution_column="contribution",
        )

    assert exc_info.value.details["target_kind"] == "attribution_frame"
    assert exc_info.value.details["missing"] == ["source_delta"]
    assert exc_info.value.details["available_columns"] == ["country", "contribution"]


def test_promote_attribution_frame_rejects_non_delta_source():
    session = mv.session.create(name="demo")
    metric = _promoted_scalar_metric(session, 30.0)
    scratch = mv.from_pandas(
        pd.DataFrame({"country": ["US"], "contribution": [10.0]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_attribution_frame(
            scratch,
            session=session,
            source_delta=mv.ArtifactRef(metric.ref),
            driver_field="country",
            contribution_column="contribution",
        )

    assert exc_info.value.details["target_kind"] == "attribution_frame"
    assert exc_info.value.details["ambiguous"] == [f"not_delta_frame:{metric.ref}"]


def test_promote_attribution_frame_fails_for_missing_driver_column():
    session = mv.session.create(name="demo")
    delta = _promoted_delta(session)
    scratch = mv.from_pandas(pd.DataFrame({"contribution": [10.0]}), session=session)

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_attribution_frame(
            scratch,
            session=session,
            source_delta=mv.ArtifactRef(delta.ref),
            driver_field="country",
            contribution_column="contribution",
        )

    assert exc_info.value.details["missing"] == ["country"]


def test_promote_attribution_frame_fails_for_non_numeric_contribution():
    session = mv.session.create(name="demo")
    delta = _promoted_delta(session)
    scratch = mv.from_pandas(
        pd.DataFrame({"country": ["US"], "contribution": ["bad"]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_attribution_frame(
            scratch,
            session=session,
            source_delta=mv.ArtifactRef(delta.ref),
            driver_field="country",
            contribution_column="contribution",
        )

    assert "non_numeric:contribution" in exc_info.value.details["ambiguous"]


def test_promote_attribution_frame_fails_for_null_contribution():
    session = mv.session.create(name="demo")
    delta = _promoted_delta(session)
    scratch = mv.from_pandas(
        pd.DataFrame({"country": ["US", "CA"], "contribution": [10.0, float("nan")]}),
        session=session,
    )

    with pytest.raises(mv.errors.PromotionFailedError) as exc_info:
        mv.promote_attribution_frame(
            scratch,
            session=session,
            source_delta=mv.ArtifactRef(delta.ref),
            driver_field="country",
            contribution_column="contribution",
        )

    assert exc_info.value.details["target_kind"] == "attribution_frame"
    assert "contribution_null" in exc_info.value.details["ambiguous"]


def test_promote_attribution_frame_lineage_digest_includes_method_and_params():
    session = mv.session.create(name="demo")
    delta = _promoted_delta(session)
    scratch = mv.from_pandas(
        pd.DataFrame({"country": ["US"], "value": [10.0], "contribution": [10.0]}),
        session=session,
    )

    manual = mv.promote_attribution_frame(
        scratch,
        session=session,
        source_delta=mv.ArtifactRef(delta.ref),
        driver_field="country",
        value_column="value",
        contribution_column="contribution",
        method="manual",
        params={"note": "manual"},
    )
    model = mv.promote_attribution_frame(
        scratch,
        session=session,
        source_delta=mv.ArtifactRef(delta.ref),
        driver_field="country",
        value_column="value",
        contribution_column="contribution",
        method="model",
        params={"note": "model"},
    )

    assert manual.lineage.steps[-1].params_digest != model.lineage.steps[-1].params_digest
