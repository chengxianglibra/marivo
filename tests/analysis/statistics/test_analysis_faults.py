"""Representative result families preserve receipts and atomic failures."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import runs_execution
from marivo.analysis.materialization.cell_arrow import logical_table
from marivo.analysis.materialization.deviation_execution import load, save
from marivo.analysis.materialization.graph_exchange import (
    ExchangePart,
    from_pandas,
    to_arrow,
)
from tests.analysis.statistics.journeys import (
    check,
    graphs,
    inputs,
    prepare,
    proof,
    snapshot,
)
from tests.shared_fixtures import DslCaseFactory
from tests.support.json import Json

RECOVERY_METHODS = (
    "deviation.zscore@v1",
    "time.runs@v1",
    "association.pearson@v1",
    "forecast.naive@v1",
)


def test_source_parts_versions_keys_scope_and_finding_authority(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """Mutate each authority separately while retaining the other valid parts."""
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    method = "forecast.naive@v1"
    result = graphs(inputs(session))[method].execute()
    check(result, method)
    assert result._dataset is not None
    retained = result._dataset.verified()
    saved = snapshot(result)
    scenarios: list[Json] = []

    def reject(primary: pd.DataFrame, parts: tuple[ExchangePart, ...]) -> None:
        with pytest.raises(AnalysisError):
            from_pandas(primary, retained.contract, parts=parts, method_state=retained.method_state)

    # Required-part removal and corruption are covered for every result family
    # by test_every_receipt_part_read_recovery_and_hit.
    # The Finding policy carries its own closed version and ordered input binding.
    policy = next(p for p in retained.parts if p.role == "finding_policy")
    payload = json.loads(policy.table["finding_policy__retained"].iloc[0])
    for scenario, field, value in (
        ("version", "version", "v999"),
        ("finding_cap", "emitted", 1001),
        ("finding_input", "ordered_input_bindings", ["capture:foreign-input"]),
    ):
        altered = {**payload, field: value}
        damaged = ExchangePart(
            "finding_policy",
            pd.DataFrame(
                {"finding_policy__retained": [json.dumps(altered, separators=(",", ":"))]}
            ),
        )
        reject(
            retained.primary, tuple(damaged if p.role == policy.role else p for p in retained.parts)
        )
        scenarios.append(scenario)
    key = retained.primary.columns[0]
    original_keys = retained.primary[key].tolist()
    assert original_keys
    wrong_keys = original_keys.copy()
    wrong_keys[0] = "foreign-output-coordinate" if isinstance(wrong_keys[0], str) else 999999
    damaged_primary = retained.primary.copy()
    damaged_primary[key] = wrong_keys
    reject(damaged_primary, retained.parts)
    scenarios.append("key")
    captured = next(
        p
        for p in retained.parts
        if p.role in ("fit_inputs", "condition_cells", "pair_inputs", "training_inputs")
    )
    captured_payload = json.loads(captured.table.iloc[0, 0])
    if "signature" in captured_payload:
        captured_payload["signature"]["domain"]["binding"]["scope_id"] = "foreign-scope"
    else:
        captured_payload["declaration"]["input_domain"]["binding"]["scope_id"] = "foreign-scope"
    damaged = ExchangePart(
        captured.role,
        pd.DataFrame(
            {captured.role + "__retained": [json.dumps(captured_payload, separators=(",", ":"))]}
        ),
    )
    reject(
        retained.primary, tuple(damaged if p.role == captured.role else p for p in retained.parts)
    )
    scenarios.append("scope")
    descriptor = result._dataset.artifact.descriptor
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


def test_run_recovery_rejects_frozen_damage_and_owned_field_changes(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    timed = inputs(session)[2]
    result = timed.runs(where=mv.any_of(timed.value.lt(2), timed.value.gt(2))).execute()
    assert result._dataset is not None
    retained = result._dataset.verified()
    _, state = runs_execution._decode(retained.parts)
    original = load(state.views)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("recovery resegmented original cells")

    monkeypatch.setattr(runs_execution, "compute", forbidden)
    for fault in ("missing_interval", "duration", "schema"):
        rows = original.to_pylist()
        assert rows
        if fault == "missing_interval":
            rows.clear()
        elif fault == "duration":
            from datetime import timedelta

            rows[0]["duration"] += timedelta(days=1)
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
                pd.DataFrame(
                    {"run_cells__retained": [runs_execution.RUNS.dump_json(damaged).decode()]}
                ),
            )
            if p.role == "run_cells"
            else p
            for p in retained.parts
        )
        from_pandas(
            retained.primary,
            retained.contract,
            parts=retained.parts,
            method_state=retained.method_state,
        )
        with pytest.raises(AnalysisError):
            from_pandas(
                retained.primary, retained.contract, parts=parts, method_state=retained.method_state
            )

    cases = (
        (
            result.where(
                result.start.value.lt(datetime(2026, 8, 2, tzinfo=timezone.utc))
            ).execute(),
            1,
        ),
        (result.where(result.count.value.gt(1)).execute(), 0),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("validation resegmented original cells")

    monkeypatch.setattr(runs_execution, "compute", forbidden)
    for result, expected_rows in cases:
        for view in (result, result.count, result.start, result.end, result.duration):
            assert view._dataset is not None
            retained = view._dataset.verified()
            assert list(retained.primary.columns) == ["key_0", "value"]
            assert len(retained.primary) == expected_rows
            logical = logical_table(to_arrow(retained.primary, retained.contract.schema))
            checked = from_pandas(
                retained.primary,
                retained.contract,
                parts=retained.parts,
                method_state=retained.method_state,
            )
            assert logical_table(to_arrow(checked.primary, retained.contract.schema)).equals(
                logical
            )
            if not expected_rows:
                continue
            value_type = retained.contract.schema.field("value").type
            damaged = retained.primary.copy()
            if pa.types.is_integer(value_type):
                damaged["value"] = damaged["value"] + 1
            else:
                from datetime import timedelta

                damaged["value"] = damaged["value"] + timedelta(microseconds=1)
            with pytest.raises(
                AnalysisError,
                match="run owned field differs from original retained intervals",
            ):
                from_pandas(
                    damaged,
                    retained.contract,
                    parts=retained.parts,
                    method_state=retained.method_state,
                )


def test_every_receipt_part_read_recovery_and_hit(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    originals = inputs(session)
    fixed_inputs = tuple(v.execute() for v in originals)
    fixed = (fixed_inputs[0], fixed_inputs[1], fixed_inputs[2])
    for method in RECOVERY_METHODS:
        logical = graphs(fixed)[method]
        result = logical.execute()
        check(result, method)
        assert result._dataset is not None
        retained = result._dataset.verified()
        descriptor = result._dataset.artifact.descriptor
        saved = snapshot(result)
        artifact_ref = result.state.artifact_ref
        before = session.runs().items
        part_role = {
            "deviation.zscore@v1": "fit_state",
            "time.runs@v1": "run_cells",
            "association.pearson@v1": "pair_inputs",
            "forecast.naive@v1": "training_inputs",
        }[method]
        part = next(part for part in retained.parts if part.role == part_role)
        for parts in (
            tuple(p for p in retained.parts if p.role != part.role),
            tuple(
                ExchangePart(p.role, pd.DataFrame({p.role + "__retained": ["{}"]}))
                if p.role == part.role
                else p
                for p in retained.parts
            ),
        ):
            try:
                from_pandas(
                    retained.primary,
                    retained.contract,
                    parts=parts,
                    method_state=retained.method_state,
                )
            except AnalysisError:
                pass
            else:
                pytest.fail(f"accepted removed or corrupt required part {part.role}")

        receipt_role, damage = {
            "deviation.zscore@v1": ("primary", None),
            "time.runs@v1": ("run_cells", b"damaged statistical receipt"),
            "association.pearson@v1": ("primary", b"damaged statistical receipt"),
            "forecast.naive@v1": ("training_inputs", None),
        }[method]
        receipt = (
            descriptor.primary_receipt.local
            if receipt_role == "primary"
            else next(p.local for p in descriptor.parts if p.role == receipt_role)
        )
        path: Path = (
            case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        )
        original = path.read_bytes()
        if damage is None:
            path.unlink()
        else:
            path.write_bytes(damage)
        try:
            for action in (
                lambda reference=artifact_ref: session.artifact(reference),
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


def test_resources_atomic_publication_and_native_failure(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    logical = graphs(inputs(session))["deviation.zscore@v1"]
    previous = logical.execute()
    saved = snapshot(previous)
    runtime = session._runtime
    successful_runs = 0
    for fault in ("cancel", "bad_batch", "before_commit", "native_failure"):
        now = [0.0]

        def inject(
            point: str, injected_fault: str = fault, injected_now: list[float] = now
        ) -> None:
            if point == "before_commit" and injected_fault == "before_commit":
                raise RuntimeError("injected statistical publication failure")
            if point == "graph_admitted" and injected_fault == "cancel":
                raise KeyboardInterrupt("injected statistical cancellation")
            if point == "before_commit" and injected_fault == "native_failure":
                raise TimeoutError("injected native source failure")

        runtime._hook = inject
        succeeded = False
        try:
            with monkeypatch.context() as scoped:
                if fault == "bad_batch":
                    from marivo.datasource.adapters import CompiledRead, SourceSession

                    def bad_result(_source: SourceSession, _read: CompiledRead) -> pd.DataFrame:
                        return pd.DataFrame({"unexpected": [1]})

                    scoped.setattr(SourceSession, "execute", bad_result)
                if succeeded:
                    result = logical.execute()
                    check(result, "deviation.zscore@v1")
                    assert result.state.artifact_ref != previous.state.artifact_ref
                    successful_runs += 1
                else:
                    with pytest.raises((AnalysisError, DomainPreparationError, KeyboardInterrupt)):
                        logical.execute()
        finally:
            runtime._hook = None
        assert snapshot(previous) == saved
        assert runtime.store.resources(session.id) == ()
        with runtime.store._read() as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0]
                == 1 + successful_runs
            )
            assert (
                connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0]
                == 1 + successful_runs
            )
            assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == (
                previous.evidence_digest().finding_count * (1 + successful_runs)
            )
        directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
        if directory:
            row = proof(previous, "deviation.zscore@v1", "produce", "table")
            row["scenarios"] = [{"before_commit": "transaction"}.get(fault, fault)]
            Path(directory).mkdir(parents=True, exist_ok=True)
            (Path(directory) / f"fault-deviation.zscore@v1-{fault}.json").write_text(
                json.dumps(row, sort_keys=True) + "\n"
            )
