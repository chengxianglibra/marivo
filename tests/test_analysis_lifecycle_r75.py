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
from marivo.analysis.materialization.history_execution import HISTORY
from marivo.refs import ref
from tests.lifecycle_r75_fixtures import END, START, build_lifecycle_public
from tests.lifecycle_r75_oracle import expected_histories

SNAPSHOT = json.loads(
    Path("docs/superpowers/specs/2026-10-01-marivo-r71-consumer-snapshot.json").read_text()
)["qualification_target"]
PROFILES = [(key, time) for key in SNAPSHOT["key_profiles"] for time in SNAPSHOT["time_profiles"]]
KEYS = {"string": "s", "int64": "i", "composite(string,int64)": "c"}


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


@pytest.mark.runtime
@pytest.mark.parametrize(
    "key,time", PROFILES, ids=[key["id"] + "-" + time["id"] for key, time in PROFILES]
)
def test_p10_source_fixed_cold_270(tmp_path, key, time):
    subject, occurrence = KEYS[key["subject"]], KEYS[key["occurrence"]]
    session, members, window, claims, values = build_lifecycle_public(
        tmp_path,
        subject=subject,
        occurrence=occurrence,
        form="table" if time["source_form"] == "duckdb_native_table" else "parquet",
        unit=time["occurrence_unit"],
        zone=time["report_timezone"],
    )
    source = replay(session, members, window, claims).execute()
    assert records(source) == expected_histories(values, subject, occurrence)
    fixed = session.artifact(source.state.artifact_ref)
    assert records(fixed) == records(source)
    assert render(fixed.contract().show) == render(source.contract().show)
    payload = {
        "session": session.id,
        "artifact": source.state.artifact_ref.ref,
        "part": source._dataset.verified().parts[0].table.to_pylist(),
        "frame": source.to_pandas().to_json(),
        "contract": render(source.contract().show),
        "show": render(source.show),
        "cell": "P10/" + key["id"] + "/" + time["id"],
    }
    (tmp_path / "r75.json").write_text(json.dumps(payload))
    (tmp_path / "source.duckdb").unlink()
    for path in tmp_path.glob("*.parquet"):
        path.unlink()
    completed = subprocess.run(
        [sys.executable, "-m", "tests.lifecycle_r75_worker", str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1])["accepted"]


@pytest.mark.runtime
@pytest.mark.parametrize("coverage", ["unknown", "prefix", "complete"])
def test_independent_full_trace_with_origin_coverage(tmp_path, coverage):
    session, members, window, claims, values = build_lifecycle_public(tmp_path)
    known = (
        None
        if coverage == "unknown"
        else START + timedelta(seconds=12)
        if coverage == "prefix"
        else END
    )
    supplied = () if known is None else (replace(claims[0], complete_through=known),)
    assert records(replay(session, members, window, supplied).execute()) == expected_histories(
        values, known=known
    )


@pytest.mark.runtime
def test_missing_origin_does_not_seed_observed_inception(tmp_path):
    session, members, window, _, _ = build_lifecycle_public(tmp_path)
    output = records(replay(session, members, window, ()).execute())
    assert all(
        row["inception"] is None
        and row["intervals"] == row["transitions"] == row["violations"] == []
        for row in output
    )
    assert {entry["disposition"] for row in output for entry in row["evaluations"]} == {
        "unknown_origin"
    }


@pytest.mark.runtime
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


@pytest.mark.runtime
def test_proved_terminal_group_survives_incomplete_followup(tmp_path):
    values = [
        (0, "started", -10, 1),
        (0, "finished", 5, 2),
        (0, "paid", 15, 3),
        (0, "pulse", 15, 4),
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
    row = records(fixed)[0]
    assert row["classification"] == "coverage_censored"
    assert len(row["violations"]) == 2
    assert all(
        entry["terminal_set"] and entry["before"] == entry["after"] == "done"
        for entry in row["violations"]
    )
    assert records(session.artifact(fixed.state.artifact_ref)) == records(fixed)


@pytest.mark.runtime
def test_empty_domain(tmp_path):
    session, members, window, claims, _ = build_lifecycle_public(tmp_path, empty=True)
    fixed = replay(session, members, window, claims).execute()
    assert records(fixed) == []
    assert len(session.artifact(fixed.state.artifact_ref).to_pandas()) == 0


@pytest.mark.runtime
def test_complete_trigger_without_inception_fails(tmp_path):
    from marivo.analysis.errors import AnalysisError

    session, members, window, claims, _ = build_lifecycle_public(tmp_path, rows=[(0, "paid", 1, 1)])
    with pytest.raises(AnalysisError, match="inception"):
        replay(session, members, window, claims).execute()


@pytest.mark.runtime
@pytest.mark.parametrize("terminal", [False, True])
def test_ties_only_after_proved_terminal(tmp_path, terminal):
    from marivo.analysis.errors import AnalysisError

    values = (
        [(0, "started", -10, 1), (0, "finished", -5, 2)] if terminal else [(0, "started", -10, 1)]
    )
    values += [(0, "paid", 10, 3), (0, "pulse", 10, 4)]
    session, members, window, claims, _ = build_lifecycle_public(
        tmp_path, rows=values, ordered=False
    )
    if not terminal:
        with pytest.raises(AnalysisError, match="business_order"):
            replay(session, members, window, claims).execute()
    else:
        item = records(replay(session, members, window, claims).execute())[0]
        assert {entry["occurrence"]["event"] for entry in item["violations"]} == {
            "commerce.paid",
            "commerce.pulse",
        }
        assert all(entry["terminal_set"] for entry in item["violations"])
        with pytest.raises(AnalysisError, match="business_order"):
            replay(session, members, window, ()).execute()


def test_frozen_public_signature_and_disclosure():
    import inspect

    import marivo
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
    text = render(lambda: marivo.help("analysis.lifecycle.replay"))
    assert "population" in text and "LogicalHistoryResult" in text
    assert "LogicalLifecycleDataset" not in text


@pytest.mark.runtime
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


@pytest.mark.runtime
@pytest.mark.parametrize("seed", range(10))
def test_scalar_oracle_varied_occurrence_order(tmp_path, seed):
    import random

    randomizer = random.Random(seed)
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


@pytest.mark.runtime
def test_same_terminal_state_different_violation_identity_is_ambiguous(tmp_path):
    from marivo.analysis.errors import AnalysisError

    session, members, window, claims, _ = build_lifecycle_public(
        tmp_path,
        ordered=False,
        rows=[(0, "started", -10, 1), (0, "finished", 10, 2), (0, "pulse", 10, 3)],
    )
    with pytest.raises(AnalysisError, match="business_order"):
        replay(session, members, window, claims).execute()


@pytest.mark.runtime
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


@pytest.mark.runtime
@pytest.mark.parametrize(
    "fault",
    [
        "trace",
        "transition",
        "interval",
        "coverage",
        "class",
        "key",
        "order",
        "extra",
        "omitted_subject",
        "missing_part",
        "version",
        "input_binding",
        "precision",
        "record_precision",
        "key_overflow",
    ],
)
def test_closed_history_exchange_rejects_semantic_corruption(tmp_path, fault):
    import pyarrow as pa

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    fixed = replay(session, members, window, claims).execute()._dataset.verified()
    primary, contract, parts = fixed.primary, fixed.contract, fixed.parts
    rows = parts[0].table.to_pylist()
    item = json.loads(rows[0]["history__record"])
    if fault == "record_precision":
        point = item["evaluations"][0]["occurrence"]["occurred_at"].replace("Z", ".000000123Z")
        item["evaluations"][0]["occurrence"]["occurred_at"] = point
        item["pre_inception"][0]["occurred_at"] = point
    elif fault == "key_overflow":
        item["evaluations"][0]["occurrence"]["key"] = [2**63]
        item["pre_inception"][0]["key"] = [2**63]
    elif fault == "trace":
        item["evaluations"][2]["after"] = "done"
    elif fault == "transition":
        item["transitions"].pop()
    elif fault == "interval":
        item["intervals"][0]["observed_ticks"] += 1
    elif fault == "coverage":
        item["known_through"] = None
    elif fault == "class":
        item["classification"] = "not_started"
    elif fault == "key":
        item["subject"] = [9007199254740994]
    elif fault == "order":
        item["evaluations"][4:6] = reversed(item["evaluations"][4:6])
    elif fault == "extra":
        item["unknown_field"] = True
    elif fault == "omitted_subject":
        primary = primary.slice(0, 2)
    elif fault == "missing_part":
        parts = ()
    elif fault in ("input_binding", "precision"):
        metadata = dict(primary.schema.metadata)
        metadata[b"r7.capture_authority" if fault == "input_binding" else b"r7.precision"] = b"[]"
        primary = primary.replace_schema_metadata(metadata)
        contract = replace(contract, schema=primary.schema)
    if fault not in ("missing_part", "version"):
        rows[0]["history__record"] = json.dumps(item)
        parts = (ExchangePart("history", pa.Table.from_pylist(rows, schema=parts[0].table.schema)),)
    with pytest.raises(AnalysisError):
        if fault == "version":
            contract = replace(
                contract,
                signature=replace(
                    contract.signature, parts=(replace(contract.signature.parts[0], version="v2"),)
                ),
            )
        from_arrow(primary, contract, parts=parts, method_state=fixed.method_state)


@pytest.mark.runtime
@pytest.mark.parametrize("role", ["primary", "history"])
@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_receipt_recovery_rejects_before_new_run(tmp_path, role, fault):
    from marivo.analysis.errors import AnalysisError

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    fixed = replay(session, members, window, claims).execute()
    reference = fixed.state.artifact_ref
    descriptor = fixed._dataset.artifact.descriptor
    receipt = descriptor.primary_receipt.local if role == "primary" else descriptor.parts[0].local
    path = tmp_path / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt canonical History")
    before = session.runs().items
    with pytest.raises(AnalysisError):
        session.artifact(reference)
    assert session.runs().items == before


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ["swap", "binding", "state_version", "model", "missing_part"])
def test_descriptor_tampering_rejects_in_independent_cold_process(tmp_path, fault):
    from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode

    session, members, window, claims, values = build_lifecycle_public(tmp_path)
    logical = replay(session, members, window, claims)
    left, right = logical.execute(), logical.execute()
    left_ref, right_ref = left.state.artifact_ref.ref, right.state.artifact_ref.ref
    payload = json.loads(encode(left._dataset.artifact.descriptor, DESCRIPTOR))
    if fault == "swap":
        other = json.loads(encode(right._dataset.artifact.descriptor, DESCRIPTOR))
        payload["parts"][0] = other["parts"][0]
    elif fault == "binding":
        payload["parts"][0]["input_binding"] = "foreign input binding"
    elif fault == "state_version":
        payload["method_state"]["contract_version"] = 2
    elif fault == "model":
        payload["signature"]["parts"][0]["preparation"]["model"]["definition"]["states"][0][
            "name"
        ] = "foreign state"
    else:
        payload["parts"] = []
    store = session._runtime.store
    with store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), left_ref),
        )
    (tmp_path / "r75.json").write_text(
        json.dumps({"session": session.id, "artifact": left_ref, "reject": True})
    )
    (tmp_path / "source.duckdb").unlink()
    completed = subprocess.run(
        [sys.executable, "-m", "tests.lifecycle_r75_worker", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["rejected"]
    assert records(session.artifact(right_ref)) == expected_histories(values)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point",
    [
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
        "cancel",
        "deadline",
    ],
)
def test_publication_failure_preserves_prior_history(tmp_path, point):
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    logical = replay(session, members, window, claims)
    fixed = logical.execute()
    expected = records(fixed)

    def inject(actual):
        if actual == ("insert_findings" if point in ("cancel", "deadline") else point):
            if point == "cancel":
                raise KeyboardInterrupt("injected History cancellation")
            if point == "deadline":
                CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
            else:
                raise RuntimeError("injected History transaction failure")

    session._runtime._hook = inject
    with pytest.raises((AnalysisError, KeyboardInterrupt)):
        logical.execute()
    session._runtime._hook = None
    assert records(session.artifact(fixed.state.artifact_ref)) == expected
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
def test_reevaluation_source_first_sharing_and_fixed_preservation(tmp_path, monkeypatch, form):
    import duckdb
    import pyarrow.parquet as pq

    from marivo.analysis.materialization import history_execution
    from marivo.datasource.adapters import SourceSession

    session, members, window, claims, _ = build_lifecycle_public(tmp_path, form=form)
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
        if form == "parquet":
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


@pytest.mark.runtime
@pytest.mark.parametrize("edition", ["docs", "zh-cn/docs"])
def test_executed_latest_history_example(tmp_path, edition):
    import re

    import marivo.semantic as ms

    session, _, window, claims, _ = build_lifecycle_public(tmp_path)
    source = Path("site/src/content/docs") / edition / "latest/concepts/analysis-workflow.mdx"
    blocks = re.findall(r"```python\n(.*?)```", source.read_text(), re.S)
    code = next(block for block in blocks if "history = session.lifecycle.replay(" in block)
    namespace = {
        "session": session,
        "window": window,
        "lifecycle_completeness": claims,
        "mv": mv,
        "ms": ms,
    }
    exec(code, namespace)
    assert isinstance(namespace["recovered"], mv.MaterializedHistoryResult)
    assert records(namespace["recovered"]) == records(namespace["retained_history"])


def test_legacy_history_retirement_preserves_shared_owners():
    from marivo.analysis.session import _lazy_sources

    assert not hasattr(_lazy_sources, "LazySources")
    for name in (
        "compiler/lifecycle.py",
        "compiler/lifecycle_array.py",
        "materialization/lifecycle_bundle.py",
        "materialization/lifecycle_integrity.py",
        "compiler/lifecycle_reducers.py",
        "domains/lifecycle_reducers.py",
        "materialization/lifecycle_reducer_codec.py",
        "materialization/lifecycle_codec.py",
        "materialization/lifecycle_publication.py",
        "domains/lifecycle.py",
    ):
        assert not (Path("marivo/analysis") / name).exists()
    for name in ("forecast_models.py",):
        assert (Path("marivo/analysis") / name).exists()
    from marivo.analysis._capabilities.registry import REGISTRY

    assert "lifecycle_dataset" not in REGISTRY.canonical_ids()
    assert not hasattr(REGISTRY, "families")


def test_p10_requirement_ids_and_phase_boundaries_are_preserved():
    import base64
    import zlib

    frozen = json.loads(
        Path("docs/superpowers/specs/2026-10-01-marivo-r71-consumer-snapshot.json").read_text()
    )
    inventory = json.loads(zlib.decompress(base64.b64decode(frozen["inventory_payload"]["data"])))
    original = {row[0] for row in inventory["qualification_cells"] if row[1] == "P10"}
    record = json.loads(
        Path("docs/superpowers/specs/2026-10-02-marivo-r75-qualification.json").read_text()
    )
    assert {item["requirement_id"] for item in record["qualification_cells"]} == original
    assert len(original) == 270
    assert all(
        item["profile_requirements"] == record["profile_requirements"]
        for item in record["qualification_cells"]
    )
    assert "unimplemented" in record["requirement_dispositions"]["V10"]
    assert "same-wheel" in record["exclusions"]


@pytest.mark.runtime
def test_public_replay_full_subject_domain(tmp_path):
    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    logical = session.lifecycle.replay(
        ref.state_model("commerce.model"),
        population=members,
        window=window,
        seed=mv.from_inception(),
        completeness=claims,
    )
    assert isinstance(logical, mv.LogicalHistoryResult)
    fixed = logical.execute()
    assert isinstance(fixed, mv.MaterializedHistoryResult)
    rows = fixed._dataset.verified()
    histories = [
        HISTORY.validate_json(row["history__record"]) for row in rows.parts[0].table.to_pylist()
    ]
    assert [item.classification for item in histories] == ["seeded", "seeded", "not_started"]
    first = histories[0]
    assert len(first.transitions) == 3
    assert [(item.from_state, item.to_state) for item in first.transitions] == [
        ("open", "open"),
        ("open", "paid"),
        ("paid", "done"),
    ]
    assert len(first.violations) == 3
    assert len(first.pre_inception) == 1
    assert [item.state for item in first.intervals] == ["open", "open", "done"]
    assert first.intervals[0].left_clipped
    assert first.intervals[0].start == START
    assert histories[2].intervals == ()
    assert fixed.to_pandas().shape[0] == 3
    restored = session.artifact(fixed.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedHistoryResult)
    assert restored.to_pandas().equals(fixed.to_pandas())
