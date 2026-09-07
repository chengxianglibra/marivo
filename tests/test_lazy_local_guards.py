"""Independent complete-input, allocation, output and hard-worker guard tests."""

import os
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.materialization import local
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import (
    LocalBudget,
    LocalPolicy,
    collect_part,
    collect_primary,
    execute_suffix,
    frame_bytes,
)
from marivo.analysis.materialization.local_worker import LocalRequest, StreamInput, supervise
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.row import RowCall, execute_row
from tests.lazy_local_fixtures import REVENUE, primary_frame, row_call, setup_local


def _table(count: int) -> pa.Table:
    return pa.table(
        {
            "entity_identity": pa.array(
                [{"id": index} for index in range(count)], type=pa.struct([("id", pa.int64())])
            ),
            "revenue": pa.array([float(index) for index in range(count)]),
        }
    )


@pytest.mark.parametrize("count", [3, 4])
def test_complete_primary_input_at_row_bound_and_above(tmp_path: Path, count: int) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    policy = replace(LocalPolicy(), max_input_rows=3)
    budget = LocalBudget(policy, time.monotonic() + 60)
    if count == 4:
        with pytest.raises(MaterializationError, match="input row overflow"):
            collect_primary(
                _table(count).to_batches(max_chunksize=1),
                source.row_contract,
                source.row_set_contract,
                budget,
            )
    else:
        result = collect_primary(
            _table(count).to_batches(max_chunksize=1),
            source.row_contract,
            source.row_set_contract,
            budget,
        )
        assert result.num_rows == count


def test_combined_required_part_is_counted_before_consumer(tmp_path: Path) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    table = _table(3)
    schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("denominator", pa.float64(), nullable=False),
        ]
    )
    part = pa.Table.from_arrays([pa.array([1, 2, 3]), pa.array([10.0, 20.0, 30.0])], schema=schema)
    total = table.nbytes + part.nbytes
    for maximum in (total, total - 1):
        budget = LocalBudget(replace(LocalPolicy(), max_input_bytes=maximum), time.monotonic() + 60)
        collect_primary(table.to_batches(), source.row_contract, source.row_set_contract, budget)
        if maximum < total:
            with pytest.raises(MaterializationError, match="combined input overflow"):
                collect_part(part.to_batches(), schema, ("id",), budget)
        else:
            assert collect_part(part.to_batches(), schema, ("id",), budget).num_rows == 3


def test_independent_source_inputs_share_one_combined_budget(tmp_path: Path) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    table = _table(3)
    budget = LocalBudget(
        replace(LocalPolicy(), max_input_bytes=table.nbytes * 2 - 1), time.monotonic() + 60
    )
    collect_primary(table.to_batches(), source.row_contract, source.row_set_contract, budget)
    with pytest.raises(MaterializationError, match="combined input overflow"):
        collect_primary(table.to_batches(), source.row_contract, source.row_set_contract, budget)


def test_late_schema_failure_never_admits_a_partial_input(tmp_path: Path) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    valid = _table(2).to_batches()[0]
    invalid = pa.record_batch([valid.column(0), pa.array(["x", "y"])], names=valid.schema.names)
    with pytest.raises(MaterializationError, match="logical type mismatch"):
        collect_primary(
            [valid, invalid],
            source.row_contract,
            source.row_set_contract,
            LocalBudget(LocalPolicy(), time.monotonic() + 60),
        )


def test_dictionary_decode_is_guarded_before_expansion() -> None:
    dictionary = pa.array(["x" * 1000])
    array = pa.DictionaryArray.from_arrays(pa.array([0] * 100, type=pa.int8()), dictionary)
    batch = pa.record_batch([array], names=["text"])
    policy = replace(LocalPolicy(), max_batch_bytes=4096)
    with pytest.raises(MaterializationError, match="decod"):
        collect_part(
            [batch],
            pa.schema([("text", pa.string())]),
            (),
            LocalBudget(policy, time.monotonic() + 60),
        )


@pytest.mark.parametrize("guard", ["method", "intermediate", "output_rows", "output_bytes"])
def test_method_intermediate_and_output_limits(
    tmp_path: Path, guard: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    frame = primary_frame(source, [10.0, 20.0, 30.0])
    call = row_call(source.where(gt(REVENUE, 0)))
    settings = {
        "method": {"max_method_rows": 2},
        "intermediate": {"max_intermediate_bytes": 1},
        "output_rows": {"max_output_rows": 2},
        "output_bytes": {"max_output_bytes": 1},
    }
    policy = replace(LocalPolicy(), **settings[guard])
    invoked = []
    original = execute_row

    def step(value: pd.DataFrame, request: RowCall) -> pd.DataFrame:
        invoked.append(True)
        return original(value, request)

    monkeypatch.setattr(local, "execute_row", step)
    with pytest.raises(MaterializationError):
        execute_suffix(
            frame,
            (call,),
            LocalBudget(policy, time.monotonic() + 60, live_bytes=frame_bytes(frame)),
        )
    assert bool(invoked) == (guard in ("output_rows", "output_bytes"))


@pytest.mark.parametrize("mode", ["timeout", "memory", "exit", "partial_reply"])
def test_supervisor_terminates_blocked_allocating_and_failed_workers(
    tmp_path: Path, mode: str
) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    policy = replace(
        LocalPolicy(),
        deadline_seconds=0.5 if mode in ("timeout", "partial_reply") else 5,
        max_worker_rss=20_000_000 if mode == "memory" else 536_870_912,
    )
    request = LocalRequest(
        StreamInput(source.row_contract, source.row_set_contract),
        (row_call(source.where(gt(REVENUE, 0))),),
        policy,
        time.monotonic() + policy.deadline_seconds,
    )
    terminal = []
    code = {
        "timeout": "import time; time.sleep(30)",
        "memory": "import time; value=bytearray(64*1024*1024); time.sleep(30)",
        "exit": "import os; os._exit(3)",
        "partial_reply": (
            "import os,sys,struct,time; "
            "os.write(int(sys.argv[1]), struct.pack('!i', 1000) + b'x'); time.sleep(30)"
        ),
    }[mode]
    started = time.monotonic()
    with pytest.raises(MaterializationError):
        supervise(
            request,
            _table(1).to_batches(),
            cancel_source=lambda: None,
            terminal=lambda: terminal.append(True),
            worker_code=code,
        )
    assert terminal == [True]
    assert time.monotonic() - started < 8


@pytest.mark.parametrize(
    "field,value",
    [("max_input_rows", 0), ("max_worker_rss", -1), ("deadline_seconds", float("inf"))],
)
def test_invalid_operational_limits_fail(field: str, value: float) -> None:
    with pytest.raises(MaterializationError, match="invalid local policy"):
        if field == "deadline_seconds":
            replace(LocalPolicy(), deadline_seconds=value)
        else:
            replace(LocalPolicy(), **{field: int(value)})


def test_registered_part_read_checks_exact_role_schema_hash_and_bounds(tmp_path: Path) -> None:
    from marivo.analysis.materialization.storage import (
        PartWriteSpec,
        ReadPolicy,
        read_part_batches,
        write_local_dataset,
    )

    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    table = _table(3).append_column("denominator", pa.array([10.0, 20.0, 30.0]))
    output = write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "stage",
        final_path=tmp_path / "output",
        batches=table.to_batches(),
        row_contract=source.row_contract,
        row_set_contract=source.row_set_contract,
        parts=(
            PartWriteSpec(
                "selected_state",
                "metric.sufficient_components",
                1,
                ("entity_identity", "denominator"),
            ),
        ),
        event=lambda point: None,
    )
    selected = output.retained_parts[0]
    schema = pa.schema(
        [("entity_identity", pa.struct([("id", pa.int64())])), ("denominator", pa.float64())]
    )
    budget = LocalBudget(LocalPolicy(), time.monotonic() + 60)
    result = collect_part(
        read_part_batches(tmp_path, selected, expected_schema=schema),
        schema,
        ("entity_identity",),
        budget,
    )
    assert result["denominator"].to_pylist() == [10.0, 20.0, 30.0]
    with pytest.raises(MaterializationError, match="row count exceeds"):
        list(
            read_part_batches(
                tmp_path, selected, expected_schema=schema, policy=ReadPolicy(max_rows=2)
            )
        )
    with pytest.raises(MaterializationError, match="schema"):
        list(
            read_part_batches(
                tmp_path, selected, expected_schema=pa.schema([("wrong", pa.int64())])
            )
        )
    data = tmp_path / selected.storage_receipt.project_relative_path / "data.parquet"
    data.unlink()
    with pytest.raises(MaterializationError):
        list(read_part_batches(tmp_path, selected, expected_schema=schema))


def test_exact_timestamp_decimal_and_integer_part_types() -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("when", pa.timestamp("us", tz="UTC")),
            pa.field("value", pa.decimal128(12, 2)),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array([1, 2], type=pa.int64()),
            pa.array(
                [datetime(2026, 1, 1, tzinfo=timezone.utc), None], type=schema.field("when").type
            ),
            pa.array([Decimal("10.25"), None], type=schema.field("value").type),
        ],
        schema=schema,
    )
    budget = LocalBudget(LocalPolicy(), time.monotonic() + 60)
    assert collect_part(table.to_batches(), schema, ("id",), budget).equals(table)
    for wrong in (
        pa.schema(
            [schema.field("id"), pa.field("when", pa.timestamp("ms")), schema.field("value")]
        ),
        pa.schema(
            [schema.field("id"), schema.field("when"), pa.field("value", pa.decimal128(12, 3))]
        ),
    ):
        with pytest.raises(MaterializationError, match="schema mismatch"):
            collect_part(
                table.to_batches(),
                wrong,
                ("id",),
                LocalBudget(LocalPolicy(), time.monotonic() + 60),
            )
    widened = pa.table({"id": pa.array([2**63], type=pa.uint64())})
    with pytest.raises(MaterializationError, match="schema mismatch"):
        collect_part(
            widened.to_batches(),
            pa.schema([("id", pa.int64())]),
            ("id",),
            LocalBudget(LocalPolicy(), time.monotonic() + 60),
        )


def test_storage_stream_above_default_local_cap_cannot_be_consumed(tmp_path: Path) -> None:
    from marivo.analysis.materialization.local_worker import ArtifactInput
    from marivo.analysis.materialization.storage import write_local_dataset

    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    table = _table(100_001)
    written = write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "stream-stage",
        final_path=tmp_path / "stream-output",
        batches=table.to_batches(max_chunksize=1024),
        row_contract=source.row_contract,
        row_set_contract=source.row_set_contract,
        event=lambda point: None,
    )
    assert written.realized_row_count == 100_001
    request = LocalRequest(
        ArtifactInput(
            tmp_path, written.primary_receipt, source.row_contract, source.row_set_contract
        ),
        (row_call(source.where(gt(REVENUE, 0))),),
        LocalPolicy(),
        time.monotonic() + 60,
    )
    terminal = []
    with pytest.raises(MaterializationError, match="row count exceeds"):
        supervise(request, (), cancel_source=lambda: None, terminal=lambda: terminal.append(True))
    assert terminal == [True]


def test_combined_arrow_allocations_share_the_intermediate_limit() -> None:
    table = pa.table({"value": [1, 2, 3]})
    policy = replace(LocalPolicy(), max_intermediate_bytes=2 * table.nbytes - 1)
    budget = LocalBudget(policy, time.monotonic() + 60)
    collect_part(table.to_batches(), table.schema, (), budget)
    with pytest.raises(MaterializationError, match="intermediate allocation overflow"):
        collect_part(table.to_batches(), table.schema, (), budget)


@pytest.mark.parametrize("setting", ["max_method_rows", "max_output_rows", "max_output_bytes"])
def test_exact_local_method_and_output_budget_boundaries(tmp_path: Path, setting: str) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    frame = primary_frame(source, [10.0, 20.0, 30.0])
    call = row_call(source.where(gt(REVENUE, 0)))
    bound = frame_bytes(frame) if setting == "max_output_bytes" else 3
    for allowance in (bound, bound - 1):
        budget = LocalBudget(
            replace(LocalPolicy(), **{setting: allowance}),
            time.monotonic() + 60,
            live_bytes=frame_bytes(frame),
        )
        if allowance == bound:
            result, _ = execute_suffix(frame, (call,), budget)
            pd.testing.assert_frame_equal(result, frame)
        else:
            with pytest.raises(MaterializationError):
                execute_suffix(frame, (call,), budget)


def test_allocation_and_deadline_exact_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = LocalBudget(replace(LocalPolicy(), max_intermediate_bytes=256), 100.0)
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    budget.allocation(256)
    with pytest.raises(MaterializationError, match="allocation overflow"):
        budget.allocation(257)
    monkeypatch.setattr(time, "monotonic", lambda: 100.000001)
    with pytest.raises(MaterializationError, match="deadline exceeded"):
        budget.check()


@pytest.mark.parametrize("excess", [0, 1])
def test_worker_peak_rss_exact_boundary(tmp_path: Path, excess: int) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    policy = LocalPolicy()
    request = LocalRequest(
        StreamInput(source.row_contract, source.row_set_contract),
        (row_call(source.where(gt(REVENUE, -1))),),
        policy,
        time.monotonic() + 60,
    )
    worker = (
        "import sys\nfrom types import SimpleNamespace\n"
        "import marivo.analysis.materialization.local_worker as worker\n"
        f"peak={policy.max_worker_rss}+{excess}*(1 if sys.platform=='darwin' else 1024)\n"
        "worker.resource.getrusage=lambda kind: SimpleNamespace(ru_maxrss=peak if sys.platform=='darwin' else peak/1024)\n"
        "worker.worker_entry()\n"
    )
    terminal = []
    if excess:
        with pytest.raises(MaterializationError, match="memory overflow"):
            supervise(
                request,
                _table(1).to_batches(),
                cancel_source=lambda: None,
                terminal=lambda: terminal.append(True),
                worker_code=worker,
            )
    else:
        result = supervise(
            request,
            _table(1).to_batches(),
            cancel_source=lambda: None,
            terminal=lambda: terminal.append(True),
            worker_code=worker,
        )
        assert result.table.num_rows == 1 and result.peak_rss == policy.max_worker_rss
    assert terminal == [True]


def test_early_stream_guard_survives_a_broken_input_pipe(tmp_path: Path) -> None:
    from collections.abc import Iterator

    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    request = LocalRequest(
        StreamInput(source.row_contract, source.row_set_contract),
        (row_call(source.where(gt(REVENUE, 0))),),
        replace(LocalPolicy(), max_input_rows=1),
        time.monotonic() + 15,
    )
    # The second batch fails admission while a later send exceeds pipe capacity.
    large = _table(20_000).to_batches()[0]
    sent = []

    def batches() -> Iterator[pa.RecordBatch]:
        yield from _table(2).to_batches(max_chunksize=1)
        for _ in range(100):
            sent.append(True)
            yield large

    terminal = []
    with pytest.raises(MaterializationError, match="input row overflow") as caught:
        supervise(
            request,
            batches(),
            cancel_source=lambda: None,
            terminal=lambda: terminal.append(True),
        )
    assert caught.value.stage == "transfer_guard"
    assert caught.value.expected == "complete input within row budget"
    assert sent and len(sent) < 100
    assert terminal == [True]


def test_worker_resource_never_uses_dead_parent_as_termination_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import resources

    worker = resources.worker_reservation("run_test")
    assert not resources.execution_is_terminal(worker)
    resources.prove_local_termination(worker)
    assert resources.execution_is_terminal(worker)
    monkeypatch.setattr(os, "getpid", lambda: 123456789)
    monkeypatch.setattr(
        os,
        "kill",
        lambda *args: pytest.fail("worker proof must not probe parent liveness"),
    )
    assert not resources.execution_is_terminal(worker)
