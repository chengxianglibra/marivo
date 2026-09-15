"""Real comparison execution keeps repeated Dimension members in distinct time buckets."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.evidence._dataset_types import DeltaFindingValueV1, Finding
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import statistics
from tests.test_lazy_adapter_runtime_acceptance import _manifest
from tests.test_lazy_compare_runtime import _record

pytestmark = pytest.mark.runtime


def _series(sources: LazySources, *, current: bool) -> LogicalMetricDataset:
    return (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(
                start="2026-02-03" if current else "2026-02-01",
                end="2026-02-05" if current else "2026-02-03",
            ),
        )
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
        .aggregate()
    )


def _check_rows_and_findings(
    result: MaterializedDeltaDataset,
) -> tuple[pd.DataFrame, tuple[Finding, ...]]:
    frame = result.to_pandas()
    keys = [("a", 0), ("a", 1), ("b", 0), ("b", 1)]
    assert list(zip(frame["channel"], frame["comparison_ordinal"], strict=True)) == keys
    assert not frame.duplicated(["channel", "comparison_ordinal"]).any()
    assert frame["current_value"].tolist() == [10.0, 3.0, 2.0, 13.0]
    assert frame["baseline_value"].tolist() == [4.0, 7.0, 9.0, 5.0]
    assert frame["delta"].tolist() == [6.0, -4.0, -7.0, 8.0]
    assert frame["relative_delta"].tolist() == pytest.approx([6 / 4, -4 / 7, -7 / 9, 8 / 5])
    assert frame["current_time"].tolist() == [date(2026, 2, 3), date(2026, 2, 4)] * 2
    assert frame["baseline_time"].tolist() == [date(2026, 2, 1), date(2026, 2, 2)] * 2
    assert frame["coordinate_presence"].tolist() == ["matched"] * 4
    assert frame["calculation_status"].tolist() == ["ok"] * 4
    findings = result.findings().items
    assert len(findings) == 4
    assert len({finding.canonical_item_key for finding in findings}) == 4
    field_names = {field.field_id: field.name for field in result.schema.columns}
    expected = [("b", 1, 8.0), ("b", 0, -7.0), ("a", 0, 6.0), ("a", 1, -4.0)]
    for finding, (channel, ordinal, delta) in zip(findings, expected, strict=True):
        assert isinstance(finding.value, DeltaFindingValueV1)
        assert finding.value.delta == delta
        coordinates = {field_names[item.field_id]: item.value for item in finding.coordinates}
        assert coordinates == {
            "channel": channel,
            "comparison_ordinal": ordinal,
            "current_time": date(2026, 2, 3 + ordinal),
            "baseline_time": date(2026, 2, 1 + ordinal),
        }
    return frame, findings


def test_repeated_dimension_members_keep_exact_paired_time_rows_in_both_routes(
    tmp_path: Path,
) -> None:
    candidate = _manifest()
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    # Two current and two baseline buckets for each member; one baseline total
    # deliberately spans multiple source rows so the reference includes aggregation.
    data = [
        (1, "a", "2026-02-01", 1.0),
        (2, "a", "2026-02-01", 3.0),
        (3, "a", "2026-02-02", 7.0),
        (4, "b", "2026-02-01", 9.0),
        (5, "b", "2026-02-02", 5.0),
        (6, "a", "2026-02-03", 10.0),
        (7, "a", "2026-02-04", 3.0),
        (8, "b", "2026-02-03", 2.0),
        (9, "b", "2026-02-04", 13.0),
    ]
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, channel, day, amount) VALUES (?, ?, ?, ?)", data
        )
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "repeated-dimension-time-comparison")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    current, baseline = _series(sources, current=True), _series(sources, current=False)
    source = current.compare(baseline).execute()
    source_frame, source_findings = _check_rows_and_findings(source)
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    source_statistics = statistics(runtime)
    retained_current, retained_baseline = current.execute(), baseline.execute()
    database.rename(tmp_path / "warehouse.offline")
    retained = retained_current.compare(retained_baseline).execute()
    retained_frame, retained_findings = _check_rows_and_findings(retained)
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    assert retained_frame.equals(source_frame)
    assert [finding.canonical_item_key for finding in retained_findings] == [
        finding.canonical_item_key for finding in source_findings
    ]
    assert runtime.store.resources(runtime.session_ref) == ()
    for route, artifact, findings, execution in (
        ("source", source, source_findings, source_statistics),
        ("retained", retained, retained_findings, statistics(runtime)),
    ):
        _record(
            f"repeated-dimension-time-{route}",
            candidate,
            {
                "shape": "dimension-time",
                "route": route,
                "artifact": artifact.state.artifact_ref.ref,
                "row_keys": [["a", 0], ["a", 1], ["b", 0], ["b", 1]],
                "current_times": ["2026-02-03", "2026-02-04"] * 2,
                "baseline_times": ["2026-02-01", "2026-02-02"] * 2,
                "deltas": [6.0, -4.0, -7.0, 8.0],
                "finding_keys": [finding.canonical_item_key for finding in findings],
                "statistics": execution,
            },
        )
