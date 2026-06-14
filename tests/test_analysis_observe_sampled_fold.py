"""Observe planning enforcement and two-phase execution for sampled semi-additive (time_fold) metrics."""

from __future__ import annotations

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe_errors import ObservePlanningError
from marivo.semantic.catalog import SemanticKind, SemanticRef


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    """Seed bandwidth_samples with 12 five-minute sample points per day across two days.

    Layout per sample point (slot index 0..11, at :00, :05, ..., :55):

    - beijing device_a: upstream_bw = 100  (constant across all slots)
    - beijing device_b: upstream_bw = 200  (constant across all slots)
      -> spatial sum per slot = 300, mean over 12 slots = 300

    - shanghai device_c: upstream_bw = 90   (constant across all slots)
      -> spatial sum per slot = 90, mean over 12 slots = 90

    For min/max/first/last variants the `upstream_bw_var` column carries
    slot-varying values so that:
      per-slot spatial sums = 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120
      => min=10, max=120, first=10, last=120

    To achieve this, only beijing device_a carries the varying value;
    all other rows contribute 0.

    Data is seeded for 2026-01-01 and 2026-01-02 to support compare/decompose
    tests that need two days of data.
    """
    con.raw_sql(
        "CREATE TABLE bandwidth_samples ("
        "sample_id INTEGER, dt DATE, sample_ts TIMESTAMP, "
        "upstream_bw DOUBLE, upstream_bw_var DOUBLE, reserved_bw DOUBLE, province VARCHAR)"
    )
    rows = []
    sid = 1
    for day in ("2026-01-01", "2026-01-02"):
        for i in range(12):
            minute = i * 5
            ts = f"TIMESTAMP '{day} 00:{minute:02d}:00'"
            # beijing: device_a (100) + device_b (200) = 300 per slot
            rows.append(f"({sid}, DATE '{day}', {ts}, 100.0, {(i + 1) * 10.0}, 200.0, 'beijing')")
            sid += 1
            rows.append(f"({sid}, DATE '{day}', {ts}, 200.0, 0.0, 0.0, 'beijing')")
            sid += 1
            # shanghai: device_c (90) per slot, 0 for upstream_bw_var
            rows.append(f"({sid}, DATE '{day}', {ts}, 90.0, 0.0, 0.0, 'shanghai')")
            sid += 1
    con.raw_sql("INSERT INTO bandwidth_samples VALUES " + ",".join(rows))


def _bootstrap_bandwidth(tmp_path):
    semantic_dir = tmp_path / "marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "bandwidth_samples = ms.entity(\n"
        "    name='bandwidth_samples',\n"
        "    datasource='warehouse',\n"
        "    primary_key=['sample_id'],\n"
        "    source=ms.table('bandwidth_samples'),\n"
        ")\n"
        "\n"
        "@ms.time_dimension(entity=bandwidth_samples, data_type='date', granularity='day')\n"
        "def dt(bandwidth_samples):\n"
        "    return bandwidth_samples.dt.cast('date')\n"
        "\n"
        "@ms.time_dimension(\n"
        "    name='sample_ts',\n"
        "    entity=bandwidth_samples,\n"
        "    data_type='datetime',\n"
        "    granularity='minute',\n"
        "    sample_interval=(5, 'minute'),\n"
        ")\n"
        "def sample_ts(bandwidth_samples):\n"
        "    return bandwidth_samples.sample_ts\n"
        "\n"
        "@ms.dimension(entity=bandwidth_samples)\n"
        "def province(bandwidth_samples):\n"
        "    return bandwidth_samples.province\n"
        "\n"
        "@ms.metric(\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity='semi_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    verification_mode='python_native',\n"
        "    time_fold='mean',\n"
        "    status_time_dimension=sample_ts,\n"
        ")\n"
        "def upstream_bw(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw.sum()\n"
        "\n"
        "@ms.metric(\n"
        "    name='upstream_bw_min',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity='semi_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    verification_mode='python_native',\n"
        "    time_fold='min',\n"
        "    status_time_dimension=sample_ts,\n"
        ")\n"
        "def upstream_bw_min(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw_var.sum()\n"
        "\n"
        "@ms.metric(\n"
        "    name='upstream_bw_max',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity='semi_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    verification_mode='python_native',\n"
        "    time_fold='max',\n"
        "    status_time_dimension=sample_ts,\n"
        ")\n"
        "def upstream_bw_max(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw_var.sum()\n"
        "\n"
        "@ms.metric(\n"
        "    name='upstream_bw_first',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity='semi_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    verification_mode='python_native',\n"
        "    time_fold='first',\n"
        "    status_time_dimension=sample_ts,\n"
        ")\n"
        "def upstream_bw_first(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw_var.sum()\n"
        "\n"
        "@ms.metric(\n"
        "    name='upstream_bw_last',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity='semi_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    verification_mode='python_native',\n"
        "    time_fold='last',\n"
        "    status_time_dimension=sample_ts,\n"
        ")\n"
        "def upstream_bw_last(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw_var.sum()\n"
        "\n"
        "@ms.metric(\n"
        "    name='upstream_bw_p95',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity='semi_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    verification_mode='python_native',\n"
        "    time_fold=('quantile', 0.95),\n"
        "    status_time_dimension=sample_ts,\n"
        ")\n"
        "def upstream_bw_p95(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw_var.sum()\n"
        "\n"
        "@ms.metric(\n"
        "    name='reserved_bw',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity='semi_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    verification_mode='python_native',\n"
        "    time_fold='mean',\n"
        "    status_time_dimension=sample_ts,\n"
        ")\n"
        "def reserved_bw(bandwidth_samples):\n"
        "    return bandwidth_samples.reserved_bw.sum()\n"
        "\n"
        "ms.derived_metric(\n"
        "    name='p95_utilization',\n"
        "    decomposition=ms.ratio(numerator='sales.upstream_bw_p95', denominator='sales.reserved_bw'),\n"
        ")\n"
    )


def _backends(con):
    return {"warehouse": lambda: con}


@pytest.fixture()
def sampled_bandwidth_project(tmp_path):
    _bootstrap_bandwidth(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    return s


def test_folded_metric_rejects_observe_with_different_time_dimension(
    sampled_bandwidth_project,
) -> None:
    session = sampled_bandwidth_project

    with pytest.raises(ObservePlanningError) as exc_info:
        session.observe(
            SemanticRef("sales.upstream_bw", kind=SemanticKind.METRIC),
            timescope={"start": "2026-01-01", "end": "2026-01-02"},
            grain="day",
            time_dimension=SemanticRef("sales.bandwidth_samples.dt", kind=SemanticKind.DIMENSION),
        )

    assert exc_info.value.details["code"] == "status-time-dimension-mismatch"


def test_sampled_mean_fold_aggregates_space_then_time(sampled_bandwidth_project) -> None:
    session = sampled_bandwidth_project

    frame = session.observe(
        SemanticRef("sales.upstream_bw", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01T00:00:00", "end": "2026-01-01T01:00:00"},
        grain="hour",
        dimensions=[SemanticRef("sales.bandwidth_samples.province", kind=SemanticKind.DIMENSION)],
    )

    df = frame.to_pandas().sort_values(["bucket_start", "province"]).reset_index(drop=True)
    assert df[["province", "upstream_bw"]].to_dict("records") == [
        {"province": "beijing", "upstream_bw": 300.0},
        {"province": "shanghai", "upstream_bw": 90.0},
    ]
    assert frame.meta.fold["time_fold"] == "mean"
    assert frame.meta.fold["status_time_dimension"] == "sales.bandwidth_samples.sample_ts"
    assert frame.meta.reaggregatable is False


def test_sampled_fold_rejects_grain_finer_than_effective_floor(sampled_bandwidth_project) -> None:
    session = sampled_bandwidth_project

    with pytest.raises(ObservePlanningError) as exc_info:
        session.observe(
            SemanticRef("sales.upstream_bw", kind=SemanticKind.METRIC),
            timescope={"start": "2026-01-01T00:00:00", "end": "2026-01-01T01:00:00"},
            grain=(1, "minute"),
            dimensions=[
                SemanticRef("sales.bandwidth_samples.province", kind=SemanticKind.DIMENSION)
            ],
        )

    assert exc_info.value.details["code"] == "grain-finer-than-sampled-floor"


@pytest.mark.parametrize(
    ("metric_ref", "expected"),
    [
        ("sales.upstream_bw_min", 10.0),
        ("sales.upstream_bw_max", 120.0),
        ("sales.upstream_bw_first", 10.0),
        ("sales.upstream_bw_last", 120.0),
    ],
)
def test_sampled_non_quantile_folds(
    metric_ref: str, expected: float, sampled_bandwidth_project
) -> None:
    frame = sampled_bandwidth_project.observe(
        SemanticRef(metric_ref, kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01T00:00:00", "end": "2026-01-01T01:00:00"},
        grain="hour",
    )
    df = frame.to_pandas()
    col_name = metric_ref.rsplit(".", 1)[1]
    assert df[col_name].iloc[0] == expected


def test_sampled_fold_persists_time_slot_coverage_sidecar(sampled_bandwidth_project) -> None:
    frame = sampled_bandwidth_project.observe(
        SemanticRef("sales.upstream_bw", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01T00:00:00", "end": "2026-01-01T01:00:00"},
        grain="hour",
    )

    assert "actual_samples" not in frame.columns
    coverage = frame.coverage()
    coverage_df = coverage.to_pandas()
    assert coverage_df[
        ["actual_samples", "expected_samples", "coverage_ratio", "coverage_status"]
    ].to_dict("records") == [
        {
            "actual_samples": 12,
            "expected_samples": 12,
            "coverage_ratio": 1.0,
            "coverage_status": "complete",
        }
    ]
    assert frame.meta.coverage_ref == coverage.ref
    assert frame.meta.quality.sample_coverage_min == 1.0


def test_sampled_quantile_fold_uses_space_aggregated_series(sampled_bandwidth_project) -> None:
    session = sampled_bandwidth_project

    frame = session.observe(
        SemanticRef("sales.upstream_bw_p95", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01T00:00:00", "end": "2026-01-01T01:00:00"},
        grain="hour",
    )

    df = frame.to_pandas()
    assert df["upstream_bw_p95"].iloc[0] == pytest.approx(114.5)
    assert frame.meta.quantile_mode == "exact"
    assert frame.meta.quantile_method == "linear_interpolation"


def test_sampled_ratio_uses_folded_components_and_min_coverage(sampled_bandwidth_project) -> None:
    frame = sampled_bandwidth_project.observe(
        SemanticRef("sales.p95_utilization", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01T00:00:00", "end": "2026-01-01T01:00:00"},
        grain="hour",
    )

    df = frame.to_pandas()
    assert df["p95_utilization"].iloc[0] == pytest.approx(0.5725)
    coverage_df = frame.coverage().to_pandas()
    assert coverage_df["coverage_ratio"].iloc[0] == 1.0
    components = frame.components().to_pandas()
    assert {"upstream_bw_p95", "reserved_bw"}.issubset(set(components["component_metric_id"]))


def test_rollup_rejects_non_reaggregatable_folded_frame(sampled_bandwidth_project) -> None:
    from marivo.analysis.errors import TransformShapeUnsupportedError

    frame = sampled_bandwidth_project.observe(
        SemanticRef("sales.upstream_bw_p95", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01", "end": "2026-01-02"},
        grain="hour",
        dimensions=[SemanticRef("sales.bandwidth_samples.province", kind=SemanticKind.DIMENSION)],
    )

    with pytest.raises(TransformShapeUnsupportedError) as exc_info:
        sampled_bandwidth_project.transform.rollup(
            frame,
            drop_axes=[SemanticRef("province", kind=SemanticKind.DIMENSION)],
        )

    assert exc_info.value.details["op"] == "rollup"
    assert exc_info.value.details["reason"] == "non_reaggregatable"


def test_decompose_rejects_non_linear_fold_delta(sampled_bandwidth_project) -> None:
    from marivo.analysis.errors import ComponentDecompositionError

    cur = sampled_bandwidth_project.observe(
        SemanticRef("sales.upstream_bw_p95", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-02", "end": "2026-01-03"},
        dimensions=[SemanticRef("sales.bandwidth_samples.province", kind=SemanticKind.DIMENSION)],
    )
    base = sampled_bandwidth_project.observe(
        SemanticRef("sales.upstream_bw_p95", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01", "end": "2026-01-02"},
        dimensions=[SemanticRef("sales.bandwidth_samples.province", kind=SemanticKind.DIMENSION)],
    )
    delta = sampled_bandwidth_project.compare(cur, base)

    with pytest.raises(ComponentDecompositionError) as exc_info:
        sampled_bandwidth_project.decompose(
            delta, axis=SemanticRef("province", kind=SemanticKind.DIMENSION)
        )

    assert exc_info.value.details["reason"] == "non_linear_time_fold"


def test_decompose_allows_mean_fold_delta(sampled_bandwidth_project) -> None:
    cur = sampled_bandwidth_project.observe(
        SemanticRef("sales.upstream_bw", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-02", "end": "2026-01-03"},
        dimensions=[SemanticRef("sales.bandwidth_samples.province", kind=SemanticKind.DIMENSION)],
    )
    base = sampled_bandwidth_project.observe(
        SemanticRef("sales.upstream_bw", kind=SemanticKind.METRIC),
        timescope={"start": "2026-01-01", "end": "2026-01-02"},
        dimensions=[SemanticRef("sales.bandwidth_samples.province", kind=SemanticKind.DIMENSION)],
    )
    delta = sampled_bandwidth_project.compare(cur, base)

    result = sampled_bandwidth_project.decompose(
        delta, axis=SemanticRef("province", kind=SemanticKind.DIMENSION)
    )
    assert result.meta.attribution_kind == "decomposition"
