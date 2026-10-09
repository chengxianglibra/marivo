"""All nine methods preserve atomicity, receipts and the shared deadline."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import runs_execution
from marivo.analysis.materialization.deviation_execution import load, save
from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline
from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow
from marivo.datasource.adapters import SourceBatchStream
from tests.analysis.statistics.journeys import (
    METHODS,
    check,
    graphs,
    inputs,
    prepare,
    proof,
    snapshot,
)
from tests.shared_fixtures import DslCaseFactory
from tests.support.json import Json


@pytest.mark.runtime
@pytest.mark.parametrize("method", METHODS)
def test_source_parts_versions_keys_scope_and_finding_authority(
    analysis_dsl_case_factory: DslCaseFactory, method: str
) -> None:
    """Mutate each authority separately while retaining the other valid parts."""
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    result = graphs(inputs(session))[method].execute()
    check(result, method)
    assert result._dataset is not None
    retained = result._dataset.verified()
    saved = snapshot(result)
    scenarios: list[Json] = []

    def reject(primary: pa.Table, parts: tuple[ExchangePart, ...]) -> None:
        with pytest.raises(AnalysisError):
            from_arrow(primary, retained.contract, parts=parts, method_state=retained.method_state)

    for part in retained.parts:
        reject(retained.primary, tuple(p for p in retained.parts if p.role != part.role))
        damaged = ExchangePart(part.role, pa.table({part.role + "__retained": ["{}"]}))
        reject(
            retained.primary, tuple(damaged if p.role == part.role else p for p in retained.parts)
        )
    scenarios.append("every_required_part")
    # The Finding policy carries its own closed version and ordered input binding.
    policy = next(p for p in retained.parts if p.role == "finding_policy")
    payload = json.loads(policy.table["finding_policy__retained"][0].as_py())
    for scenario, field, value in (
        ("version", "version", "v999"),
        ("finding_cap", "emitted", 1001),
        ("finding_input", "ordered_input_bindings", ["capture:foreign-input"]),
    ):
        altered = {**payload, field: value}
        damaged = ExchangePart(
            "finding_policy",
            pa.table({"finding_policy__retained": [json.dumps(altered, separators=(",", ":"))]}),
        )
        reject(
            retained.primary, tuple(damaged if p.role == policy.role else p for p in retained.parts)
        )
        scenarios.append(scenario)
    key = retained.primary.column_names[0]
    original_keys = retained.primary[key].to_pylist()
    assert original_keys
    wrong_keys = original_keys.copy()
    wrong_keys[0] = "foreign-output-coordinate" if isinstance(wrong_keys[0], str) else 999999
    reject(
        retained.primary.set_column(
            0, key, pa.array(wrong_keys, type=retained.primary.schema.field(key).type)
        ),
        retained.parts,
    )
    scenarios.append("key")
    captured = next(
        p
        for p in retained.parts
        if p.role in ("fit_inputs", "condition_cells", "pair_inputs", "training_inputs")
    )
    captured_payload = json.loads(captured.table[0][0].as_py())
    if "signature" in captured_payload:
        captured_payload["signature"]["domain"]["binding"]["scope_id"] = "foreign-scope"
    else:
        captured_payload["declaration"]["input_domain"]["binding"]["scope_id"] = "foreign-scope"
    damaged = ExchangePart(
        captured.role,
        pa.table(
            {captured.role + "__retained": [json.dumps(captured_payload, separators=(",", ":"))]}
        ),
    )
    reject(
        retained.primary, tuple(damaged if p.role == captured.role else p for p in retained.parts)
    )
    scenarios.append("scope")
    descriptor = result._dataset.artifact.descriptor
    receipt = descriptor.primary_receipt.local
    path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    original = path.read_bytes()
    for damage in (None, b"corrupt source primary receipt"):
        if damage is None:
            path.unlink()
        else:
            path.write_bytes(damage)
        try:
            with pytest.raises(AnalysisError):
                session.artifact(result.state.artifact_ref)
        finally:
            path.write_bytes(original)
    scenarios.append("receipt")
    # Store mutations run in a rollback transaction, so one counterexample cannot
    # invalidate the starting facts of the next counterexample.
    store = session._runtime.store
    artifact = result.state.artifact_ref.ref
    finding_count = result.evidence_digest().finding_count
    for scenario, statement, parameters in (
        (
            "finding_body",
            "UPDATE findings SET finding_body_payload=? WHERE artifact_ref=?",
            ("{}", artifact),
        ),
        (
            "finding_order",
            "UPDATE findings SET finding_ordinal=finding_ordinal+10 WHERE artifact_ref=?",
            (artifact,),
        ),
    ):
        with store._connection() as connection:
            connection.execute("BEGIN")
            if not finding_count:
                connection.execute(
                    "INSERT INTO findings(artifact_ref,finding_ordinal,finding_identity_digest,finding_body_payload,finding_ref) VALUES(?,?,?,?,?)",
                    (
                        artifact,
                        10 if scenario == "finding_order" else 0,
                        "foreign",
                        "{}",
                        "finding_foreign",
                    ),
                )
            else:
                connection.execute(statement, parameters)
            from marivo.analysis.materialization.graph_findings import collection

            try:
                with pytest.raises(AnalysisError):
                    collection(store, connection, descriptor, artifact)
            finally:
                connection.rollback()
        scenarios.append(scenario)
    assert snapshot(result) == saved
    directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
    if directory:
        row = proof(result, method, "produce", "table", saved)
        row["scenarios"] = scenarios
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / f"source-authority-{method}.json").write_text(
            json.dumps(row, sort_keys=True) + "\n"
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "fault", ("missing_interval", "duration", "input_row", "overlap", "termination", "schema")
)
def test_frozen_run_witnesses_reject_damage_without_segmenting(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    result = graphs(inputs(session))["time.runs@v1"].execute()
    assert result._dataset is not None
    retained = result._dataset.verified()
    _, state = runs_execution._decode(retained.parts)
    original = load(state.views)
    rows = original.to_pylist()
    if fault == "missing_interval":
        rows.clear()
    elif fault == "duration":
        from datetime import timedelta

        rows[0]["duration"] = timedelta(days=1)
    elif fault == "input_row":
        rows[0]["input_rows"] = [0, 1]
    elif fault == "overlap":
        rows.append(rows[0].copy())
    elif fault == "termination":
        rows[0]["left_kind"] = "scope_boundary"
    views = pa.Table.from_pylist(rows, schema=original.schema)
    if fault == "schema":
        views = views.set_column(
            views.schema.get_field_index("duration"),
            "duration",
            views["duration"].cast(pa.duration("ms")),
        )
    damaged = replace(state, views=save(views))
    parts = tuple(
        ExchangePart(
            p.role,
            pa.table({"run_cells__retained": [runs_execution.RUNS.dump_json(damaged).decode()]}),
        )
        if p.role == "run_cells"
        else p
        for p in retained.parts
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("recovery resegmented original cells")

    monkeypatch.setattr(runs_execution, "compute", forbidden)
    from_arrow(
        retained.primary,
        retained.contract,
        parts=retained.parts,
        method_state=retained.method_state,
    )
    with pytest.raises(AnalysisError):
        from_arrow(
            retained.primary, retained.contract, parts=parts, method_state=retained.method_state
        )


@pytest.mark.runtime
@pytest.mark.parametrize("method", METHODS)
def test_every_receipt_part_read_recovery_and_hit(
    analysis_dsl_case_factory: DslCaseFactory, method: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    originals = inputs(session)
    fixed_inputs = tuple(v.execute() for v in originals)
    logical = graphs((fixed_inputs[0], fixed_inputs[1], fixed_inputs[2]))[method]
    result = logical.execute()
    check(result, method)
    assert result._dataset is not None
    retained = result._dataset.verified()
    descriptor = result._dataset.artifact.descriptor
    saved = snapshot(result)
    before = session.runs().items
    for part in retained.parts:
        for parts in (
            tuple(p for p in retained.parts if p.role != part.role),
            tuple(
                ExchangePart(p.role, pa.table({p.role + "__retained": ["{}"]}))
                if p.role == part.role
                else p
                for p in retained.parts
            ),
        ):
            try:
                from_arrow(
                    retained.primary,
                    retained.contract,
                    parts=parts,
                    method_state=retained.method_state,
                )
            except AnalysisError:
                pass
            else:
                pytest.fail(f"accepted removed or corrupt required part {part.role}")
    for receipt in (descriptor.primary_receipt.local, *(p.local for p in descriptor.parts)):
        path: Path = (
            case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        )
        original = path.read_bytes()
        for damage in (None, b"damaged statistical receipt"):
            if damage is None:
                path.unlink()
            else:
                path.write_bytes(damage)
            try:
                for action in (
                    lambda: session.artifact(result.state.artifact_ref),
                    result.to_pandas,
                    logical.execute,
                ):
                    with pytest.raises(AnalysisError):
                        action()
                    assert session.runs().items == before
            finally:
                path.write_bytes(original)
    assert snapshot(result) == saved
    assert session._runtime.store.resources(session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    "fault",
    (
        "cancel",
        "close",
        "bad_batch",
        "before_commit",
        "after_commit",
        "timeout_below",
        "timeout_at",
        "timeout_above",
        "late_result",
    ),
)
def test_resources_atomic_publication_and_deadline(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    fault: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    logical = graphs(inputs(session))[method]
    previous = logical.execute()
    saved = snapshot(previous)
    runtime = session._runtime
    now = [0.0]

    def inject(point: str) -> None:
        if point == "before_commit" and fault == "before_commit":
            raise RuntimeError("injected statistical publication failure")
        if point == "graph_admitted" and fault == "cancel":
            raise KeyboardInterrupt("injected statistical cancellation")
        if point == "after_commit" and fault == "after_commit":
            raise OSError("lost statistical durable commit acknowledgement")
        if point == "before_commit" and fault in ("timeout_below", "timeout_at", "timeout_above"):
            now[0] = (
                599.999 if fault == "timeout_below" else 600.0 if fault == "timeout_at" else 600.001
            )
        if point == "graph_receipts_verified" and fault == "late_result":
            now[0] = 600.001

    runtime._hook = inject
    close = SourceBatchStream.close

    def fail_close(stream: SourceBatchStream) -> None:
        close(stream)
        raise pa.ArrowInvalid("injected statistical source close failure")

    def bad_batch(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
        yield pa.record_batch({"unexpected": [1]})

    if fault == "close":
        monkeypatch.setattr(SourceBatchStream, "close", fail_close)
    if fault == "bad_batch":
        monkeypatch.setattr(SourceBatchStream, "_iterate", bad_batch)
    token = CURRENT.set(ExecuteDeadline(0.0, lambda: now[0]))
    succeeded = fault in ("timeout_below", "timeout_at", "after_commit")
    try:
        if succeeded:
            result = logical.execute()
            check(result, method)
            assert result.state.artifact_ref != previous.state.artifact_ref
        else:
            with pytest.raises((AnalysisError, DomainPreparationError, KeyboardInterrupt)):
                logical.execute()
    finally:
        CURRENT.reset(token)
        runtime._hook = None
    assert snapshot(previous) == saved
    assert runtime.store.resources(session.id) == ()
    with runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == (
            2 if succeeded else 1
        )
        assert connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == (
            2 if succeeded else 1
        )
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[
            0
        ] == previous.evidence_digest().finding_count * (2 if succeeded else 1)
    directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
    if directory:
        row = proof(previous, method, "produce", "table")
        row["scenarios"] = [
            {"before_commit": "transaction", "after_commit": "committed_success"}.get(fault, fault)
        ]
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / f"fault-{method}-{fault}.json").write_text(
            json.dumps(row, sort_keys=True) + "\n"
        )
