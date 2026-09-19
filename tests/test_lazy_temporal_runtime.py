"""Real publication, atomic failures and source-offline temporal continuations."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("cell", ["oops", "99", "-3", "", " 15", "007"])
def test_illegal_hour_cells_are_rejected_before_publication(cell: str, tmp_path: Path) -> None:
    """A malformed string hour must fail closed as a structured rejection.

    The engine must never coerce the cell into a legal hour and publish it.
    """
    from dataclasses import replace

    from marivo.analysis import grain, time_scope
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.observation.metric import LogicalMetricDataset
    from marivo.refs import ref
    from marivo.semantic.ir import DateParse, HourPrefixParse
    from tests.lazy_temporal_fixtures import AXIS, temporal_fixture

    with temporal_fixture(
        tmp_path,
        physical="DATE",
        declared="date",
        parse=DateParse(),
        granularity="day",
        report_zone="UTC",
        values=("2026-07-01", "2026-07-01"),
    ) as fixture:
        fixture.backend.con.execute("UPDATE orders SET channel = ? WHERE id = 1", [cell])
        fixture.backend.con.execute("UPDATE orders SET channel = '15' WHERE id = 2")
        registry = replace(fixture.registry, dimensions=dict(fixture.registry.dimensions))
        registry.dimensions[AXIS] = replace(registry.dimensions[AXIS], is_default=False)
        hour = "sales.orders.hour"
        registry.dimensions[hour] = replace(
            registry.dimensions[AXIS],
            semantic_id=hour,
            name="hour",
            is_default=True,
            granularity="hour",
            parse=HourPrefixParse(AXIS),
            source_column="channel",
        )
        registry.freeze()
    store = SessionStore(tmp_path)
    session = store.create_session("hour-range", report_timezone_name="UTC")
    runtime = DatasetRuntime(store, session.session_ref)
    logical: LogicalMetricDataset = (
        runtime.sources(semantic_registry=registry, sidecar=fixture.sidecar)
        .observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
        )
        .with_time_axis(ref.time_dimension(hour), grain=grain("hour"))
        .aggregate()
    )
    with pytest.raises(MaterializationError, match=r"temporal\.hour_range") as failure:
        logical.execute()
    # A structured rejection names the contract, the physical cell and the repair.
    assert failure.value.received == "source validation failed: temporal.hour_range"
    assert "0-23" in failure.value.expected
    assert "sales.orders.hour" in str(failure.value.hint)
    assert failure.value.__cause__ is None
    assert runtime.statistics.primary_queries == 0
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


def test_boolean_hour_column_is_rejected_before_publication(tmp_path: Path) -> None:
    """A flag column must not publish its implicit 0/1 hours, nor raise a bare error."""
    from dataclasses import replace

    from marivo.analysis import grain, time_scope
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.observation.metric import LogicalMetricDataset
    from marivo.datasource.ir import TableColumnBindingIR, TableSourceIR
    from marivo.refs import ref
    from marivo.semantic.ir import DateParse, HourPrefixParse
    from tests.lazy_temporal_fixtures import AXIS, temporal_fixture

    with temporal_fixture(
        tmp_path,
        physical="DATE",
        declared="date",
        parse=DateParse(),
        granularity="day",
        report_zone="UTC",
        values=("2026-07-01", "2026-07-01"),
    ) as fixture:
        fixture.backend.raw_sql("ALTER TABLE orders ADD COLUMN bhour BOOLEAN")
        fixture.backend.con.execute("UPDATE orders SET bhour = TRUE WHERE id = 1")
        fixture.backend.con.execute("UPDATE orders SET bhour = FALSE WHERE id = 2")
        registry = replace(
            fixture.registry,
            dimensions=dict(fixture.registry.dimensions),
            entities=dict(fixture.registry.entities),
        )
        entity = registry.entities["sales.orders"]
        source = entity.source
        assert isinstance(source, TableSourceIR)
        registry.entities[entity.semantic_id] = replace(
            entity,
            source=replace(
                source,
                columns=(*source.columns, ("bhour", TableColumnBindingIR("bhour", "boolean"))),
            ),
        )
        registry.dimensions[AXIS] = replace(registry.dimensions[AXIS], is_default=False)
        hour = "sales.orders.hour"
        registry.dimensions[hour] = replace(
            registry.dimensions[AXIS],
            semantic_id=hour,
            name="hour",
            is_default=True,
            granularity="hour",
            parse=HourPrefixParse(AXIS),
            source_column="bhour",
        )
        registry.freeze()
    store = SessionStore(tmp_path)
    session = store.create_session("boolean-hour", report_timezone_name="UTC")
    runtime = DatasetRuntime(store, session.session_ref)
    logical: LogicalMetricDataset = (
        runtime.sources(semantic_registry=registry, sidecar=fixture.sidecar)
        .observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
        )
        .with_time_axis(ref.time_dimension(hour), grain=grain("hour"))
        .aggregate()
    )
    with pytest.raises(MaterializationError, match=r"temporal\.hour_range") as failure:
        logical.execute()
    assert failure.value.received == "source validation failed: temporal.hour_range"
    assert failure.value.__cause__ is None
    assert runtime.statistics.primary_queries == 0
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


def test_temporal_authority_survives_cold_continuations(tmp_path: Path) -> None:
    def run(mode: str, *args: str) -> dict[str, object]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.lazy_temporal_runtime_worker",
                mode,
                str(tmp_path),
                *args,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={
                **os.environ,
                "TZ": "Pacific/Honolulu" if mode == "recover" else "UTC",
                "MARIVO_TELEMETRY": "off",
            },
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(completed.stdout)
        assert isinstance(result, dict)
        return result

    produced = run("produce")
    (tmp_path / "warehouse.duckdb").rename(tmp_path / "warehouse.offline")
    recovered = run("recover", str(produced["session"]), str(produced["artifact"]))
    assert recovered["pid"] != produced["pid"]
    assert recovered["descriptor"] == produced["descriptor"]
    assert recovered["source_attempts"] == []


@pytest.mark.parametrize("point", ["parse", "temporal_authority", "insert_artifact"])
def test_temporal_failures_are_atomic(tmp_path: Path, point: str) -> None:
    from tests.lazy_materialization_crash_worker import snapshot
    from tests.lazy_temporal_runtime_worker import setup

    runtime, logical = setup(tmp_path, invalid_parse=point == "parse")
    hits: list[str] = []

    def fail(name: str) -> None:
        if name == point:
            hits.append(name)
            raise RuntimeError("temporal-failure-canary")

    runtime._hook = fail
    import duckdb

    with pytest.raises(
        duckdb.InvalidInputException if point == "parse" else RuntimeError
    ) as failed:
        logical.execute()
    assert "canary" in str(failed.value)
    assert hits == ([] if point == "parse" else [point])
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    counts = snapshot(runtime)["counts"]
    assert isinstance(counts, dict)
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == counts["findings"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


@pytest.mark.parametrize(
    "start,end,values,hours",
    [
        ("2026-03-08", "2026-03-09", ("2026-03-08 06:30:00", "2026-03-08 07:30:00"), 23),
        ("2026-11-01", "2026-11-02", ("2026-11-01 05:15:00", "2026-11-01 06:15:00"), 25),
    ],
)
def test_retained_cumulative_dst_coverage_is_measured_in_instants(
    tmp_path: Path,
    start: str,
    end: str,
    values: tuple[str, str],
    hours: int,
) -> None:
    from dataclasses import replace

    from marivo.analysis import grain, time_scope
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.reads import part_schema, read_part_batches
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.observation.contracts import EntityReducedMetricSemantics
    from marivo.analysis.observation.fold_contracts import coverage_columns
    from marivo.analysis.observation.metric import MaterializedMetricDataset
    from marivo.refs import ref
    from marivo.semantic.ir import CumulativeComposition, TimestampParse
    from tests.lazy_temporal_fixtures import AXIS, temporal_fixture

    with temporal_fixture(
        tmp_path,
        parse=TimestampParse(timezone="UTC"),
        report_zone="America/New_York",
        values=values,
    ) as fixture:
        registry = replace(fixture.registry, metrics=dict(fixture.registry.metrics))
        registry.metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition("sales.revenue", AXIS),
        )
        registry.freeze()
    store = SessionStore(tmp_path)
    session = store.create_session("dst", report_timezone_name="America/New_York")
    runtime = DatasetRuntime(store, session.session_ref)
    sources = runtime.sources(semantic_registry=registry, sidecar=fixture.sidecar)
    logical = (
        sources.observe(ref.metric("sales.running"), time_scope=time_scope(start=start, end=end))
        .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
        .aggregate()
    )
    materialized = logical.execute()
    assert isinstance(materialized, MaterializedMetricDataset)
    assert materialized.to_pandas()["running"].tolist() == [3]
    (tmp_path / "warehouse.duckdb").rename(tmp_path / "warehouse.offline")
    folded = materialized.rollup(drop_time=True).execute()
    assert folded.to_pandas()["running"].tolist() == [3]
    semantics = folded.row_contract.family_semantics
    assert isinstance(semantics, EntityReducedMetricSemantics)
    _, _, _, seconds, complete = coverage_columns(semantics.metric_folds[0])
    record = runtime.store.artifact(folded.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 1
    part = record.descriptor.retained_parts[0]
    batches = tuple(read_part_batches(tmp_path, part, expected_schema=part_schema(tmp_path, part)))
    assert sum(batch.num_rows for batch in batches) == 1
    state = next(batch.to_pylist()[0] for batch in batches if batch.num_rows)
    assert state[seconds] == hours * 3600
    assert state[complete] is True
