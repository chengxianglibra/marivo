"""Adversarial parity, native identity isolation and same-plan membership proofs."""

from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest
import sqlglot
from sqlglot import expressions as exp

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.domains.lifecycle import MaterializedLifecycleDataset
from marivo.analysis.domains.lifecycle_reducers import in_state
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.predicates import eq
from marivo.refs import ref
from marivo.semantic.ir import LifecycleStateIR, StateTransitionIR, StateTriggerIR
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_runtime_fixtures import journey
from tests.lazy_event_runtime_worker import assert_identity_private
from tests.lazy_lifecycle_fixtures import (
    END,
    MODEL,
    START,
    history,
    lifecycle_registry,
    setup_lifecycle,
)

pytestmark = pytest.mark.runtime


def rich_history(project: Path) -> tuple[DatasetRuntime, MaterializedLifecycleDataset]:
    runtime, sources, database = setup_lifecycle(project, engine=True)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("SET TimeZone='UTC'")
        connection.execute("DELETE FROM customers")
        connection.execute(
            "INSERT INTO customers (id, region) VALUES (101,'EU'),(102,'EU'),(103,NULL),(104,NULL),(105,'APAC'),(106,'APAC')"
        )
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
        connection.executemany(
            "INSERT INTO started_rows VALUES (?,?,?)",
            [
                (901, 101, START - timedelta(hours=1)),
                (903, 101, START + timedelta(hours=3, microseconds=1)),
                (904, 102, START),
                (905, 103, START + timedelta(hours=5)),
                (906, 105, END),
            ],
        )
        connection.executemany(
            "INSERT INTO finished_rows VALUES (?,?,?)",
            [
                (911, 101, START + timedelta(microseconds=1)),
                (912, 101, START + timedelta(hours=2)),
                (913, 101, START + timedelta(hours=3, microseconds=2)),
                (914, 101, START + timedelta(hours=4)),
            ],
        )
    registry = sources._owner.semantic_registry
    model = replace(
        registry.state_models[MODEL.path],
        states=(
            LifecycleStateIR("warm", True, False),
            LifecycleStateIR("idle", False, False),
            LifecycleStateIR("cold", False, False),
        ),
        transitions=(
            StateTransitionIR("idle", StateTriggerIR("sales.finished", "buyer"), "idle"),
            StateTransitionIR("warm", StateTriggerIR("sales.finished", "buyer"), "cold"),
            StateTransitionIR("cold", StateTriggerIR("sales.finished", "buyer"), "warm"),
        ),
    )
    registry = replace(registry, state_models={MODEL.path: model})
    registry.freeze()
    sources = runtime.sources(semantic_registry=registry, sidecar=sources._owner.sidecar)
    return runtime, history(sources).execute()


def test_local_engine_numerical_structural_and_order_parity(tmp_path: Path) -> None:
    frames: dict[str, pd.DataFrame] = {}
    for sink in ("engine", "local"):
        project = tmp_path / sink
        project.mkdir()
        runtime, h = rich_history(project)
        runtime.target = LocalTarget()
        results = {
            "distribution": h.distribution(
                at=(END, START, START + timedelta(hours=1)),
                axes=(ref.dimension("sales.customers.region"),),
            ).execute(),
            "transitions": h.transitions().execute(),
            "dwell": h.dwell().execute(),
            "violations": h.violations().execute(),
        }
        for name, result in results.items():
            frame = result.to_pandas()
            if sink == "engine":
                frames[name] = frame
            else:
                pd.testing.assert_frame_equal(frame, frames[name])
            assert list(frame.columns) == [field.name for field in result.schema.columns]
        distribution = frames["distribution"]
        expected = []
        for at in (START, START + timedelta(hours=1), END):
            for region in ("APAC", "EU", None):
                known = 2 if region == "EU" else 1 if region is None and at == END else 0
                counts = (
                    (2, 0, 0)
                    if region == "EU" and at in (START, END)
                    else (1, 0, 1)
                    if region == "EU"
                    else (known, 0, 0)
                )
                for state, count in zip(("warm", "idle", "cold"), counts, strict=True):
                    expected.append(
                        (region, at, state, count, known, 0, None if not known else count / known)
                    )
        actual = [
            tuple(None if pd.isna(v) else v for v in row)
            for row in distribution.itertuples(index=False, name=None)
        ]
        assert actual == expected
        transitions = frames["transitions"]
        assert list(transitions.itertuples(index=False, name=None)) == [
            ("idle", "idle", 0, 0.0),
            ("warm", "cold", 2, 0.5),
            ("cold", "warm", 2, 0.5),
        ]
        dwell = frames["dwell"]
        assert dwell.model_state.tolist() == ["warm", "idle", "cold"]
        assert dwell.interval_count.tolist() == [5, 0, 2]
        assert dwell.completed_count.tolist() == [2, 0, 2]
        assert dwell.right_censored_count.tolist() == [3, 0, 0]
        assert dwell.coverage_censored_count.tolist() == [0, 0, 0]
        assert dwell.left_clipped_completed_count.tolist() == [1, 0, 0]
        durations = {
            "warm": [1, 3_600_000_000 + 2],
            "cold": [7_200_000_000 - 1, 3_600_000_000 - 2],
        }
        for state, values in durations.items():
            ordered = sorted(values)
            mean = sum(Fraction(v) for v in values) / len(values)
            quantiles = []
            for q in (Fraction(1, 2), Fraction(9, 10)):
                index = q * (len(values) - 1)
                lower = index.numerator // index.denominator
                quantiles.append(
                    Fraction(ordered[lower])
                    + (index - lower) * (ordered[min(lower + 1, len(values) - 1)] - ordered[lower])
                )
            row = dwell[dwell.model_state == state].iloc[0]
            for field, expected_us in zip(
                ("mean_duration", "median_duration", "p90_duration"),
                (mean, *quantiles),
                strict=True,
            ):
                assert abs(
                    row[field] - pd.to_timedelta(float(expected_us), unit="us")
                ) <= pd.Timedelta(1, unit="ns")
        assert all(
            pd.isna(dwell.iloc[1][field])
            for field in ("mean_duration", "median_duration", "p90_duration")
        )
        violations = frames["violations"]
        assert violations.entity_identity.tolist() == [(101,)]
        assert violations.trigger_event_identity.tolist() == [(903,)]
        assert violations.violation_kind.tolist() == ["illegal_transition"]
        for name, result in results.items():
            field = (
                "from_model_state"
                if name == "transitions"
                else "violation_kind"
                if name == "violations"
                else "model_state"
            )
            selected = (
                "cold"
                if name == "transitions"
                else "illegal_transition"
                if name == "violations"
                else "warm"
            )
            filtered = result.where(eq(result.fields.get(field), selected)).execute().to_pandas()
            pd.testing.assert_frame_equal(
                filtered, frames[name][frames[name][field] == selected].reset_index(drop=True)
            )


def test_high_cardinality_identity_relations_stay_native_and_private(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM customers")
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
        connection.execute("INSERT INTO customers (id) SELECT 881730041+i FROM range(5000) t(i)")
        connection.execute(
            "INSERT INTO started_rows SELECT 981730041+i,881730041+i,TIMESTAMP '2026-02-01 00:00:00' FROM range(5000) t(i)"
        )
        connection.execute(
            "INSERT INTO finished_rows SELECT 991730041+i,881730041+i,TIMESTAMP '2026-02-01 01:00:00' FROM range(5000) t(i)"
        )
        connection.execute(
            "INSERT INTO finished_rows SELECT 997730041+i,881730041+i,TIMESTAMP '2026-02-01 02:00:00' FROM range(5000) t(i)"
        )
    with (
        patch.object(admission, "supervise", forbidden),
        patch("marivo.analysis.materialization.reads.payload_batches", forbidden),
    ):
        h = history(sources).execute()
        results = (
            h.distribution(at=(END,)).execute(),
            h.transitions().execute(),
            h.dwell().execute(),
            h.violations().execute(),
            h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END)).execute(),
        )
    records = [runtime.store.artifact(r.state.artifact_ref.ref) for r in results]
    assert all(record is not None for record in records)
    assert [
        record.descriptor.storage_receipt.realized_row_count
        for record in records
        if record is not None
    ] == [
        2,
        1,
        2,
        5000,
        5000,
    ]
    assert runtime.statistics.transferred_rows == 5000
    assert runtime.statistics.transferred_bytes > 0
    assert runtime.statistics.worker_pid is None and runtime.statistics.local_handoffs == ()
    canaries = ("881730041", "981730041", "991730041", "997730041")
    assert_identity_private(runtime, canaries)
    metadata = repr([(repr(r), r.contract(), r.evidence_digest) for r in results])
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    recovered = cold.artifact(results[3].state.artifact_ref)
    record = cold.store.artifact(recovered.state.artifact_ref.ref)
    assert record is not None
    receipt = record.descriptor.storage_receipt
    from marivo.analysis.materialization.contracts import LocalReceipt

    assert isinstance(receipt, LocalReceipt)
    (tmp_path / receipt.project_relative_path / "data.parquet").unlink()
    with pytest.raises(MaterializationError) as caught:
        recovered.to_pandas()
    captured = capsys.readouterr()
    diagnostics = metadata + str(caught.value) + caplog.text + captured.out + captured.err
    assert all(canary not in diagnostics for canary in canaries)
    assert_identity_private(cold, canaries)


@pytest.mark.parametrize("consumer", ["metric", "event", "lifecycle"])
def test_same_plan_selection_is_realized_once(tmp_path: Path, consumer: str) -> None:
    runtime, sources, _ = setup_lifecycle(tmp_path, engine=True)
    metric = sources.observe(
        ref.metric("sales.revenue"), population=sources.population(ref.entity("sales.customers"))
    )
    selected = history(sources, population=metric).select_subjects(
        in_state(ModelStateHandle(MODEL, "done"), at=END)
    )
    logical = (
        sources.observe(ref.metric("sales.revenue"), population=selected)
        if consumer == "metric"
        else journey(sources, population=selected)
        if consumer == "event"
        else history(sources, population=selected)
    )
    result = logical.execute()
    assert set(result.to_pandas().entity_identity) == {(1,)}
    assert snapshot(runtime)["dataset_artifacts"] == 1
    realizations = []
    for kind, sql in runtime.statistics.statements:
        if kind != "source_fence":
            continue
        statement = sqlglot.parse_one(sql, read="duckdb")
        assert isinstance(statement, exp.Create)
        query = statement.expression
        if (
            statement.this.name.startswith("__mv_lifecycle_reducer_")
            and isinstance(query, exp.Select)
            and query.named_selects == ["entity_identity"]
        ):
            realizations.append(statement.this.name)
    assert len(realizations) == 1
    assert (
        runtime.statistics.transferred_rows == {"metric": 1, "event": 2, "lifecycle": 5}[consumer]
    )
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize(
    "consumer", ["distribution", "transitions", "dwell", "violations", "selection"]
)
def test_unregistered_parquet_reader_is_rejected_before_data_work(
    tmp_path: Path, consumer: str
) -> None:
    runtime, sources, _ = setup_lifecycle(tmp_path)
    h = history(sources).execute()
    logical = (
        h.distribution(at=(END,))
        if consumer == "distribution"
        else h.transitions()
        if consumer == "transitions"
        else h.dwell()
        if consumer == "dwell"
        else h.violations()
        if consumer == "violations"
        else h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END))
    )
    before = snapshot(runtime)
    with (
        patch.object(admission, "_duckdb_version", "unsupported"),
        patch.object(admission, "_build_backend_from_effective", forbidden),
        patch.object(admission, "supervise", forbidden),
        pytest.raises(DatasetCompilationError, match="source-required"),
    ):
        logical.execute()
    assert runtime.last_run_ref is None
    assert snapshot(runtime) == before


@pytest.mark.parametrize("kind", ["local", "foreign_engine"])
@pytest.mark.parametrize("consumer", ["metric", "event", "lifecycle"])
def test_selected_membership_uses_registered_parquet_reader(
    tmp_path: Path, kind: str, consumer: str
) -> None:
    runtime, sources, _ = setup_lifecycle(tmp_path, engine=True)
    h = history(sources).execute()
    if kind == "local":
        runtime.target = LocalTarget()
    selected = h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END)).execute()
    if kind == "foreign_engine":
        foreign = tmp_path / "foreign.duckdb"
        import shutil

        shutil.copyfile(tmp_path / "warehouse.duckdb", foreign)
        registry, sidecar = lifecycle_registry(foreign)
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.revenue"), population=selected)
        if consumer == "metric"
        else journey(sources, population=selected)
        if consumer == "event"
        else history(sources, population=selected)
    )
    before = snapshot(runtime)
    with patch.object(admission, "supervise", forbidden):
        result = logical.execute()
    assert set(result.to_pandas().entity_identity) == {(1,)}
    assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"] + 1
    assert runtime.statistics.worker_pid is None
    assert runtime.store.resources(runtime.session_ref) == ()
