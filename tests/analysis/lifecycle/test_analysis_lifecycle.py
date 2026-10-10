"""Independent public Lifecycle replay and full retained-state acceptance."""

import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.refs import ref
from tests.analysis.lifecycle.lifecycle_fixtures import (
    RECOVERY_PROFILES,
    START,
    build_lifecycle_public,
)
from tests.analysis.lifecycle.lifecycle_oracle import expected_histories

PROFILES = RECOVERY_PROFILES
KEYS = {"string": "s", "int64": "i", "composite(string,int64)": "c"}
# Source/fixed correctness and key identity shapes are covered by ordinary tests;
# retain one standalone replay as the representative cold recovery path.
PROFILE_IDS = (("K22", "T07"),)
RECOVERY_CASES = tuple(
    (key, time) for key, time in PROFILES if (key["id"], time["id"]) in PROFILE_IDS
)
COLD_REPLAY_PROFILE = ("K22", "T07")


@pytest.fixture(scope="module")
def history_exchange_template(tmp_path_factory: pytest.TempPathFactory) -> ExchangeResult:
    """Build one immutable Arrow witness for all retained-history corruption cases."""
    root = tmp_path_factory.mktemp("lifecycle-exchange")
    session, members, window, claims, _ = build_lifecycle_public(root)
    result = replay(session, members, window, claims).execute()
    assert result._dataset is not None
    return result._dataset.verified()


def replay(session, members, window, claims):
    return session.lifecycle.replay(
        ref.state_model("commerce.model"),
        population=members,
        window=window,
        seed=mv.from_inception(),
        completeness=claims,
    )


def render(call):
    output = StringIO()
    with redirect_stdout(output):
        call()
    return output.getvalue()


def records(result):
    return [
        json.loads(row["history__record"])
        for row in result._dataset.verified().parts[0].table.to_pylist()
    ]


@pytest.mark.slow
@pytest.mark.parametrize(
    "key,time", RECOVERY_CASES, ids=[key["id"] + "-" + time["id"] for key, time in RECOVERY_CASES]
)
def test_history_replay_source_fixed_profiles_and_representative_cold(tmp_path, key, time):
    subject, occurrence = KEYS[key["subject"]], KEYS[key["occurrence"]]
    session, members, window, claims, values = build_lifecycle_public(
        tmp_path,
        subject=subject,
        occurrence=occurrence,
        form="table" if time["source_form"] == "duckdb_native_table" else "parquet",
        unit=time["occurrence_unit"],
        zone=time["report_timezone"],
    )
    logical = replay(session, members, window, claims)
    assert isinstance(logical, mv.LogicalHistoryResult)
    source = logical.execute()
    assert isinstance(source, mv.MaterializedHistoryResult)
    assert records(source) == expected_histories(values, subject, occurrence)
    fixed = session.artifact(source.state.artifact_ref)
    assert records(fixed) == records(source)
    assert fixed.to_pandas().equals(source.to_pandas())
    assert render(fixed.contract().show) == render(source.contract().show)
    if (key["id"], time["id"]) != COLD_REPLAY_PROFILE:
        return
    payload = {
        "session": session.id,
        "artifact": source.state.artifact_ref.ref,
        "part": source._dataset.verified().parts[0].table.to_pylist(),
        "frame": source.to_pandas().to_json(),
        "contract": render(source.contract().show),
        "show": render(source.show),
        "cell": "P10/" + key["id"] + "/" + time["id"],
    }
    (tmp_path / "lifecycle-recovery.json").write_text(json.dumps(payload))
    (tmp_path / "source.duckdb").unlink()
    for path in tmp_path.glob("*.parquet"):
        path.unlink()
    completed = subprocess.run(
        [sys.executable, "-m", "tests.analysis.lifecycle.lifecycle_worker", str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1])["accepted"]


def test_missing_origin_and_complete_input_without_inception(tmp_path):
    from marivo.analysis.errors import AnalysisError

    values = [(0, "paid", 1, 1)]
    session, members, window, claims, _ = build_lifecycle_public(tmp_path, rows=values)
    output = records(replay(session, members, window, ()).execute())
    assert all(
        row["inception"] is None
        and row["intervals"] == row["transitions"] == row["violations"] == []
        for row in output
    )
    assert {entry["disposition"] for row in output for entry in row["evaluations"]} == {
        "unknown_origin"
    }
    with pytest.raises(AnalysisError, match="inception"):
        replay(session, members, window, claims).execute()


def test_followup_gap_keeps_known_prefix_without_inventing_transition(tmp_path):
    values = [(0, "started", -10, 1), (0, "paid", 5, 2), (0, "finished", 15, 3)]
    session, members, window, claims, _ = build_lifecycle_public(tmp_path, rows=values)
    prefix = START + timedelta(seconds=10)
    output = records(
        replay(session, members, window, (replace(claims[0], complete_through=prefix),)).execute()
    )
    assert output == expected_histories(values, known=prefix)
    assert output[0]["evaluations"][-1]["before"] is None
    assert output[0]["evaluations"][-1]["disposition"] == "unknown_followup"
    assert len(output[0]["transitions"]) == 1


def test_proved_terminal_groups_survive_incomplete_followup(tmp_path):
    from marivo.analysis.errors import AnalysisError

    values = [
        (0, "started", -10, 1),
        (0, "finished", 5, 2),
        (0, "paid", 15, 3),
        (0, "pulse", 15, 4),
        (1, "started", -10, 1),
        (1, "finished", -5, 2),
        (1, "paid", 5, 3),
        (1, "pulse", 5, 4),
    ]
    session, members, window, claims, _ = build_lifecycle_public(
        tmp_path, rows=values, ordered=False
    )
    fixed = replay(
        session,
        members,
        window,
        (replace(claims[0], complete_through=START + timedelta(seconds=10)),),
    ).execute()
    rows_by_subject = {tuple(row["subject"]): row for row in records(fixed)}
    first = rows_by_subject[(9007199254740993,)]
    tied = rows_by_subject[(9007199254740994,)]
    assert first["classification"] == "coverage_censored"
    assert len(first["violations"]) == 2
    assert all(
        entry["terminal_set"] and entry["before"] == entry["after"] == "done"
        for entry in first["violations"]
    )
    assert len(tied["violations"]) == 2
    assert all(entry["terminal_set"] for entry in tied["violations"])
    assert records(session.artifact(fixed.state.artifact_ref)) == records(fixed)
    with pytest.raises(AnalysisError, match="business_order"):
        replay(session, members, window, ()).execute()


def test_unproved_tie_requires_business_order(tmp_path):
    from marivo.analysis.errors import AnalysisError

    values = [(0, "started", -10, 1), (0, "paid", 10, 3), (0, "pulse", 10, 4)]
    session, members, window, claims, _ = build_lifecycle_public(
        tmp_path, rows=values, ordered=False
    )
    with pytest.raises(AnalysisError, match="business_order"):
        replay(session, members, window, claims).execute()


def test_frozen_public_signature_and_disclosure():
    import inspect

    from marivo.analysis.session._history_lifecycle import HistoryLifecycle

    signature = inspect.signature(HistoryLifecycle.replay)
    assert tuple(signature.parameters) == (
        "self",
        "model",
        "population",
        "window",
        "seed",
        "completeness",
    )
    assert all(
        signature.parameters[name].default is inspect.Parameter.empty
        for name in ("model", "population", "window", "seed")
    )
    assert {"LogicalHistoryResult", "MaterializedHistoryResult"} <= set(mv.__all__)
    assert "LogicalLifecycleDataset" not in mv.__all__
    assert hasattr(mv.LogicalHistoryResult, "read")
    assert hasattr(mv.MaterializedHistoryResult, "distribution")


def test_construction_rejects_authority_before_rows_or_run(tmp_path, monkeypatch):
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    foreign, population, _, _, _ = build_lifecycle_public(tmp_path / "foreign")

    def forbidden(*args, **kwargs):
        raise AssertionError("construction read business rows")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    logical = replay(session, members, window, claims)
    from marivo.analysis.materialization.graph_snapshot import freeze_graph, thaw_graph

    assert freeze_graph(thaw_graph(freeze_graph(logical._node.root))) == freeze_graph(
        logical._node.root
    )
    with pytest.raises(TypeError):
        session.lifecycle.replay(
            ref.state_model("commerce.model"), window=window, seed=mv.from_inception()
        )
    with pytest.raises(TypeError):
        session.lifecycle.replay(
            ref.state_model("commerce.model"),
            population=members,
            window=window,
            seed=mv.from_inception(),
            business_order=ref.business_order("commerce.order"),
        )
    for model, domain, seed in (
        (ref.entity("commerce.subjects"), members, mv.from_inception()),
        (ref.state_model("commerce.absent"), members, mv.from_inception()),
        (ref.state_model("commerce.model"), population, mv.from_inception()),
        (ref.state_model("commerce.model"), members, None),
        (ref.state_model("commerce.model"), None, mv.from_inception()),
        (session.catalog.require(ref.state_model("commerce.model")), members, mv.from_inception()),
    ):
        with pytest.raises(AnalysisError):
            session.lifecycle.replay(model, population=domain, window=window, seed=seed)
    assert session.runs().items == foreign.runs().items == ()


def test_scalar_oracle_varied_occurrence_order(tmp_path):
    import random

    randomizer = random.Random(7)
    values = [(0, "started", -30, 1), (1, "started", -30, 1)]
    for subject in (0, 1):
        for sequence in range(2, 20):
            values.append(
                (
                    subject,
                    randomizer.choice(("started", "paid", "pulse", "finished")),
                    randomizer.randrange(-10, 110),
                    sequence,
                )
            )
    randomizer.shuffle(values)
    session, members, window, claims, _ = build_lifecycle_public(tmp_path, rows=values)
    assert records(replay(session, members, window, claims).execute()) == expected_histories(values)


def test_same_terminal_state_different_violation_identity_is_ambiguous(tmp_path):
    from marivo.analysis.errors import AnalysisError

    session, members, window, claims, _ = build_lifecycle_public(
        tmp_path,
        ordered=False,
        rows=[(0, "started", -10, 1), (0, "finished", 10, 2), (0, "pulse", 10, 3)],
    )
    with pytest.raises(AnalysisError, match="business_order"):
        replay(session, members, window, claims).execute()


@pytest.mark.parametrize("ordered", [True, False])
def test_cycle_same_time_trace_requires_business_order(tmp_path, ordered):
    from marivo.analysis.errors import AnalysisError

    values = [
        (0, "started", -20, 1),
        (0, "finished", 5, 2),
        (0, "finished", 10, 3),
        (0, "finished", 10, 4),
        (0, "finished", 15, 5),
    ]
    session, members, window, claims, _ = build_lifecycle_public(
        tmp_path, cycle=True, ordered=ordered, rows=values
    )
    if not ordered:
        with pytest.raises(AnalysisError, match="business_order"):
            replay(session, members, window, claims).execute()
    else:
        fixed = replay(session, members, window, claims).execute()
        assert records(fixed) == expected_histories(values, cycle=True)
        assert records(session.artifact(fixed.state.artifact_ref)) == records(fixed)


def test_closed_history_exchange_rejects_semantic_corruption(
    history_exchange_template: ExchangeResult,
) -> None:
    import pyarrow as pa

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow

    fixed = history_exchange_template
    for fault in ("trace", "missing_part", "version", "input_binding"):
        primary, contract, parts = fixed.primary, fixed.contract, fixed.parts
        rows = parts[0].table.to_pylist()
        item = json.loads(rows[0]["history__record"])
        if fault == "trace":
            item["evaluations"][2]["after"] = "done"
            rows[0]["history__record"] = json.dumps(item)
            parts = (
                ExchangePart("history", pa.Table.from_pylist(rows, schema=parts[0].table.schema)),
            )
        elif fault == "missing_part":
            parts = ()
        elif fault == "input_binding":
            metadata = dict(primary.schema.metadata)
            metadata[b"r7.capture_authority"] = b"[]"
            primary = primary.replace_schema_metadata(metadata)
            contract = replace(contract, schema=primary.schema)
        with pytest.raises(AnalysisError):
            if fault == "version":
                contract = replace(
                    contract,
                    signature=replace(
                        contract.signature,
                        parts=(replace(contract.signature.parts[0], version="v2"),),
                    ),
                )
            from_arrow(primary, contract, parts=parts, method_state=fixed.method_state)


def test_receipt_recovery_rejects_before_new_run(tmp_path):
    from marivo.analysis.errors import AnalysisError

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    fixed = replay(session, members, window, claims).execute()
    reference = fixed.state.artifact_ref
    descriptor = fixed._dataset.artifact.descriptor
    receipts = (
        (descriptor.primary_receipt.local, "missing"),
        (descriptor.parts[0].local, "corrupt"),
    )
    for receipt, fault in receipts:
        path = tmp_path / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        original = path.read_bytes()
        if fault == "missing":
            path.unlink()
        else:
            path.write_bytes(b"corrupt canonical History")
        before = session.runs().items
        try:
            with pytest.raises(AnalysisError):
                session.artifact(reference)
            assert session.runs().items == before
        finally:
            path.write_bytes(original)


def test_publication_failure_preserves_prior_history(tmp_path):
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    logical = replay(session, members, window, claims)
    fixed = logical.execute()
    expected = records(fixed)

    for point in ("before_commit", "cancel", "deadline"):
        deadline_token = None

        def inject(actual, injection_point=point):
            nonlocal deadline_token
            if actual == (
                "insert_findings" if injection_point in ("cancel", "deadline") else injection_point
            ):
                if injection_point == "cancel":
                    raise KeyboardInterrupt("injected History cancellation")
                if injection_point == "deadline":
                    deadline_token = CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
                else:
                    raise RuntimeError("injected History transaction failure")

        session._runtime._hook = inject
        try:
            with pytest.raises((AnalysisError, KeyboardInterrupt)):
                logical.execute()
        finally:
            session._runtime._hook = None
            if deadline_token is not None:
                CURRENT.reset(deadline_token)
        assert records(session.artifact(fixed.state.artifact_ref)) == expected
        with session._runtime.store._read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
            assert (
                connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0]
                == 0
            )


def test_reevaluation_source_first_sharing_and_fixed_preservation(tmp_path, monkeypatch):
    import duckdb
    import pyarrow.parquet as pq

    from marivo.analysis.materialization import history_execution
    from marivo.datasource.adapters import SourceSession

    session, members, window, claims, _ = build_lifecycle_public(tmp_path, form="parquet")
    trace = []
    original_read, original_execute = SourceSession.batches, history_execution.execute

    def read(*args, **kwargs):
        trace.append("source")
        return original_read(*args, **kwargs)

    def execute(*args, **kwargs):
        trace.append("scan")
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(SourceSession, "batches", read)
    monkeypatch.setattr(history_execution, "execute", execute)
    logical = replay(session, members, window, claims)
    assert (
        logical._node.root.inputs[0].node.identity
        == logical._node.root.inputs[1].node.inputs[0].node.identity
    )
    fixed = logical.execute()
    assert max(index for index, value in enumerate(trace) if value == "source") < trace.index(
        "scan"
    )
    expected = records(fixed)
    with duckdb.connect(str(tmp_path / "source.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE kind='finished'")
        pq.write_table(connection.table("facts").to_arrow_table(), tmp_path / "facts.parquet")
    changed = logical.execute()
    assert changed.state.artifact_ref != fixed.state.artifact_ref
    assert records(changed) != expected
    assert records(session.artifact(fixed.state.artifact_ref)) == expected
    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError, match="logical"):
        session.lifecycle.replay(
            ref.state_model("commerce.model"),
            population=members.execute(),
            window=window,
            seed=mv.from_inception(),
        )
