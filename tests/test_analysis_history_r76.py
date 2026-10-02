"""Independent History views, exact Duration and source/fixed/cold qualification."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.refs import ref
from tests.history_r76_oracle import assert_view, views
from tests.lifecycle_r75_fixtures import END, START, build_lifecycle_public

TARGET = json.loads(
    Path("docs/superpowers/specs/2026-10-01-marivo-r71-consumer-snapshot.json").read_text()
)["qualification_target"]
PROFILES = [(key, time) for key in TARGET["key_profiles"] for time in TARGET["time_profiles"]]
KEYS = {"string": "s", "int64": "i", "composite(string,int64)": "c"}
METHODS = ("in_state", "distribution", "transitions", "violations", "intervals", "dwell")


def test_r76_preserves_all_original_cells_requirements_and_later_owners():
    import base64
    import zlib

    snapshot = json.loads(
        Path("docs/superpowers/specs/2026-10-01-marivo-r71-consumer-snapshot.json").read_text()
    )
    inventory = json.loads(zlib.decompress(base64.b64decode(snapshot["inventory_payload"]["data"])))
    profiles = {"P11", "P12", "P13", "P14", "P15", "P16", "P24", "P36", "P49"}
    original = {row[0]: row for row in inventory["qualification_cells"] if row[1] in profiles}
    record = json.loads(
        Path("docs/superpowers/specs/2026-10-02-marivo-r76-qualification.json").read_text()
    )
    cells = record["qualification_cells"]
    assert len(cells) == len(original) == 2430
    assert {cell["requirement_id"] for cell in cells} == original.keys()
    requirements = {profile["id"]: profile["requirements"] for profile in TARGET["method_profiles"]}
    for cell in cells:
        source = original[cell["requirement_id"]]
        assert cell["method_profile"] == source[1]
        assert cell["key_profile"] == source[2]
        assert cell["time_profile"] == source[3]
        assert cell["phase"] == source[4]
        assert cell["original_route"] == source[5]
        assert cell["original_status"] == source[6]
        assert cell["mandatory"] == source[7]
        assert cell["status"] == "passed_bounded_local_r76"
    assert {profile["id"]: profile["requirements"] for profile in record["method_profiles"]} == {
        profile: requirements[profile] for profile in profiles
    }
    assert record["counts"]["history_views"] == 1620
    assert record["counts"]["history_consumers"] == 810
    assert record["later_responsibilities"]["P24"] == ["R7.7", "R7.8"]
    assert "same-wheel" in record["exclusions"]
    assert record["requirement_dispositions"]["V18"]["same_wheel"] == "unverified"
    for journey in record["a10"]:
        assert journey["phases"] == ["producer", "continue", "cold"]
        assert len(journey["executed_K"]) == journey["fixed_K_count"] == 109
        assert journey["fixed_outputs"] == 119
        assert journey["cold_exact_hit"] is True


def replay(session, members, window, claims):
    return session.lifecycle.replay(
        ref.state_model("commerce.model"),
        population=members,
        window=window,
        seed=mv.from_inception(),
        completeness=claims,
    )


def operations(history):
    return {
        "in_state": lambda: history.read(
            mv.in_state(
                ms.model_state(model=ref.state_model("commerce.model"), name="done"), at=END
            )
        ),
        "distribution": lambda: history.distribution(at=(START, END)),
        "transitions": history.transitions,
        "violations": history.violations,
        "intervals": history.intervals,
        "dwell": history.dwell,
    }


def artifact(result):
    return result.evidence_digest().artifact_ref.ref


@pytest.mark.runtime
@pytest.mark.parametrize(
    "key,time", PROFILES, ids=[key["id"] + "-" + time["id"] for key, time in PROFILES]
)
def test_p11_p16_source_fixed_cold_1620(tmp_path, key, time):
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
    history = logical.execute()
    expected = views(values, subject, occurrence)
    source, fixed, cells = {}, {}, []
    for index, (name, operation) in enumerate(operations(logical).items(), 11):
        result = operation().execute()
        assert_view(result, name, expected)
        source[name] = artifact(result)
        cells.append(f"P{index:02d}/{key['id']}/{time['id']}/S")
    for index, (name, operation) in enumerate(operations(history).items(), 11):
        result = operation().execute()
        assert_view(result, name, expected)
        fixed[name] = artifact(result)
        cells.extend(f"P{index:02d}/{key['id']}/{time['id']}/{route}" for route in ("F", "C"))
    payload = {
        "session": session.id,
        "history": artifact(history),
        "views": source,
        "fixed": fixed,
        "rows": values,
        "subject": subject,
        "occurrence": occurrence,
        "cells": cells,
    }
    (tmp_path / "r76.json").write_text(json.dumps(payload))
    (tmp_path / "source.duckdb").unlink()
    for path in tmp_path.glob("*.parquet"):
        path.unlink()
    result = subprocess.run(
        [sys.executable, "-m", "tests.history_r76_worker", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["accepted"]
    evidence = os.getenv("MARIVO_R76_EVIDENCE_DIR")
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (key["id"] + "-" + time["id"] + ".json")).write_text(
            json.dumps(
                {
                    "accepted": cells,
                    "form": time["source_form"],
                    "subject": subject,
                    "occurrence": occurrence,
                }
            )
        )


@pytest.mark.runtime
@pytest.mark.parametrize("coverage", ["unknown", "prefix", "complete"])
def test_checkpoint_raw_oracle_coverage_and_end(tmp_path, coverage):
    session, members, window, claims, values = build_lifecycle_public(tmp_path)
    known = (
        None
        if coverage == "unknown"
        else START + timedelta(seconds=12)
        if coverage == "prefix"
        else END
    )
    supplied = () if known is None else (replace(claims[0], complete_through=known),)
    history = replay(session, members, window, supplied).execute()
    expected = views(values, known=known)
    for name, operation in operations(history).items():
        assert_view(operation().execute(), name, expected)


@pytest.mark.runtime
def test_empty_subject_domain_retains_zero_states_pairs(tmp_path):
    session, members, window, claims, values = build_lifecycle_public(tmp_path, empty=True)
    history = replay(session, members, window, claims).execute()
    expected = views(values, empty=True)
    for name, operation in operations(history).items():
        assert_view(operation().execute(), name, expected)


@pytest.mark.runtime
def test_instance_selection_subject_image_and_duration_mean(tmp_path):
    session, members, window, claims, _ = build_lifecycle_public(
        tmp_path, subject="c", occurrence="c"
    )
    history = replay(session, members, window, claims).execute()
    intervals = history.intervals().execute()
    binding = intervals.subjects()
    selected = intervals.where(intervals.status.value.eq("completed"))
    assert len(selected.members(through=binding).execute().to_pandas()) == 1
    durations = selected.observed_duration
    mean = durations.summarize(mv.mean()).execute()
    assert mean.to_pandas()["value"].tolist() == [timedelta(seconds=5)]
    assert mean._dataset.verified().parts[0].table["row_state__sum"].to_pylist() == [10_000_000]
    empty = intervals.where(intervals.status.value.eq("missing"))
    assert empty.members(through=binding).execute().to_pandas().empty
    assert empty.observed_duration.summarize(mv.mean()).execute().to_pandas()[
        "cell_reason"
    ].tolist() == ["empty_completed_set"]
    violations = history.violations().execute()
    assert (
        len(
            violations.where(violations.kind.value.eq("transition_from_terminal"))
            .members(through=violations.subjects())
            .execute()
            .to_pandas()
        )
        == 1
    )


@pytest.mark.runtime
def test_checkpoint_axes_prepared_on_complete_subject_domain(tmp_path):
    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    history = replay(session, members, window, claims)
    result = history.distribution(
        at=(START, END), axes=(ref.dimension("commerce.subjects.sid"),)
    ).execute()
    assert len(result.to_pandas()) == 18
    assert set(result.to_pandas()["axis_0"]) == {
        9007199254740993,
        9007199254740994,
        9007199254740995,
    }
    assert result.known_state_count.execute().to_pandas()["value"].sum() == 4
    with pytest.raises(AnalysisError, match="checkpoint axes"):
        history.execute().distribution(at=(END,), axes=(ref.dimension("commerce.subjects.sid"),))
    with pytest.raises(AnalysisError, match="checkpoint"):
        history.distribution(at=(END + timedelta(microseconds=1),))


def test_fraction_quantile_half_even_and_overflow():
    from marivo.analysis.materialization.history_views import checked, quantile

    assert quantile([0, 1], Fraction(1, 2)) == 0
    assert quantile([1, 2], Fraction(1, 2)) == 2
    assert quantile([0, 5], Fraction(9, 10)) == 4
    assert quantile([], Fraction(9, 10)) is None
    assert checked(2**63 - 1) == 2**63 - 1
    with pytest.raises(AnalysisError, match="int64"):
        checked(2**63)


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["state", "interval", "violation"])
@pytest.mark.parametrize("form", ["table", "parquet"])
def test_same_run_history_selection_prepares_all_sources_before_local(
    tmp_path, monkeypatch, kind, form
):
    from marivo.analysis.materialization import history_execution
    from marivo.datasource.adapters import SourceSession
    from tests.history_r76_fixtures import build_history_public, observe

    session, members, window, claims, _ = build_history_public(
        tmp_path, form=form, subject="c", occurrence="c"
    )
    history = replay(session, members, window, claims)
    trace = []
    original_read, original_replay = SourceSession.batches, history_execution.execute

    def read(self, *args, **kwargs):
        assert "local" not in trace
        trace.append("source")
        return original_read(self, *args, **kwargs)

    def consume(*args, **kwargs):
        trace.append("local")
        return original_replay(*args, **kwargs)

    monkeypatch.setattr(SourceSession, "batches", read)
    monkeypatch.setattr(history_execution, "execute", consume)
    for metric, value in (("revenue", 33.0), ("fact_count", 6)):
        trace.clear()
        logical = observe(history, kind, metric)
        assert [a.call for a in logical.contract().actions] == ["relation.execute()"]
        result = logical.execute()
        assert result.to_pandas()["value"].tolist() == [value]
        state = result._dataset.verified()
        assert (
            state.contract.signature.domain.binding.scope_id
            == members._node.root.signature.domain.binding.scope_id
        )
        original = next(p.table for p in state.parts if p.role == "original_state")
        assert original.num_rows == 1
        component = "sum" if metric == "revenue" else "count"
        assert original["original_state__" + component].to_pylist() == [value]
        if metric == "revenue":
            assert original["original_state__non_null_count"].to_pylist() == [6]
        assert next(p.table for p in state.parts if p.role == "coverage").num_rows == 1
        assert trace.count("local") == 1


@pytest.mark.runtime
@pytest.mark.parametrize(
    "fault",
    [
        "missing_part",
        "extra_state",
        "primary",
        "subject",
        "precision",
        "version",
        "binding",
        "truncated_trace",
    ],
)
def test_view_exchange_rejects_corruption(tmp_path, fault):
    import pyarrow as pa

    from marivo.analysis.core.model import HistoryViewPart
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    fixed = replay(session, members, window, claims).intervals().execute()._dataset.verified()
    primary, contract, parts = fixed.primary, fixed.contract, list(fixed.parts)
    index = next(i for i, p in enumerate(parts) if p.role == "history_view")
    payload = json.loads(parts[index].table["history_view__retained"][0].as_py())
    if fault == "missing_part":
        parts.pop(index)
    elif fault == "extra_state":
        payload["undeclared"] = "value"
    elif fault == "primary":
        primary = primary.set_column(
            primary.schema.get_field_index("state"), "state", pa.array(["paid"] * primary.num_rows)
        )
    elif fault == "subject":
        position = next(i for i, p in enumerate(parts) if p.role == "subject")
        subjects = parts[position].table
        parts[position] = ExchangePart(
            "subject",
            subjects.set_column(
                subjects.schema.get_field_index("subject__key_0"),
                "subject__key_0",
                pa.array([42] * subjects.num_rows, type=pa.int64()),
            ),
        )
    elif fault == "precision":
        primary = primary.replace_schema_metadata(
            {**primary.schema.metadata, b"r7.precision": b"[]"}
        )
        contract = replace(contract, schema=primary.schema)
    elif fault == "truncated_trace":
        payload["histories"][0]["transitions"].pop()
    with pytest.raises(AnalysisError):
        if fault in ("version", "binding"):
            signature = replace(
                contract.signature,
                parts=tuple(
                    replace(p, version="v2")
                    if fault == "version"
                    else replace(p, binding=replace(p.binding, scope_id="foreign"))
                    if isinstance(p, HistoryViewPart)
                    else p
                    for p in contract.signature.parts
                ),
            )
            contract = replace(contract, signature=signature)
        if fault in ("extra_state", "truncated_trace"):
            parts[index] = ExchangePart(
                "history_view", pa.table({"history_view__retained": [json.dumps(payload)]})
            )
        from_arrow(primary, contract, parts=tuple(parts), method_state=fixed.method_state)


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
def test_view_atomic_failure_keeps_prior_artifact(tmp_path, point):
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    logical = replay(session, members, window, claims).dwell()
    fixed = logical.execute()
    reference = artifact(fixed)
    expected = fixed.to_pandas()

    def inject(actual):
        if actual == ("insert_findings" if point in ("cancel", "deadline") else point):
            if point == "cancel":
                raise KeyboardInterrupt("injected view cancellation")
            if point == "deadline":
                CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
            else:
                raise RuntimeError("injected view transaction failure")

    session._runtime._hook = inject
    with pytest.raises((AnalysisError, KeyboardInterrupt)):
        logical.execute()
    session._runtime._hook = None
    assert session.artifact(reference).to_pandas().equals(expected)
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0


@pytest.mark.runtime
def test_summaries_cannot_reconstruct_subjects_or_transition_trace(tmp_path):
    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    history = replay(session, members, window, claims).execute()
    intervals = history.intervals().execute()
    assert not hasattr(intervals, "transitions")
    for result in (
        history.distribution(at=(END,)).execute(),
        history.transitions().execute(),
        history.dwell().execute(),
    ):
        assert not hasattr(result, "subjects")
        assert not hasattr(result, "members")
    field = history.transitions().execute().count.execute()
    assert not {"relation.compare(baseline)", "relation.ratio(other)"} & {
        a.call for a in field.contract().actions
    }


@pytest.mark.runtime
@pytest.mark.parametrize(
    "key,time", PROFILES, ids=[key["id"] + "-" + time["id"] for key, time in PROFILES]
)
def test_p24_p36_p49_history_consumers_810(tmp_path, key, time):
    from tests.history_r76_consumers import expected, mean, observations, transport
    from tests.history_r76_fixtures import build_history_public

    session, members, window, claims, _ = build_history_public(
        tmp_path,
        subject=KEYS[key["subject"]],
        occurrence=KEYS[key["occurrence"]],
        form="table" if time["source_form"] == "duckdb_native_table" else "parquet",
        unit=time["occurrence_unit"],
        zone=time["report_timezone"],
    )
    logical = replay(session, members, window, claims)
    history = logical.execute()
    source, fixed, captured = {}, {}, {}
    for name, operation in {
        **transport(logical),
        "mean": mean(logical),
    }.items():
        result = operation.execute()
        expected(name, result)
        source[name] = artifact(result)
    for name, operation in observations(logical).items():
        result = operation.execute()
        expected(name, result)
        source[name] = captured[name] = artifact(result)
        merged = result.rollup().execute()
        expected(name, merged)
        fixed[name] = artifact(merged)
    for name, operation in {
        **transport(history),
        "mean": mean(history),
    }.items():
        result = operation.execute()
        expected(name, result)
        fixed[name] = artifact(result)
    cells = [
        f"{method}/{key['id']}/{time['id']}/{route}"
        for method in ("P24", "P36", "P49")
        for route in ("S", "F", "C")
    ]
    (tmp_path / "consumers.json").write_text(
        json.dumps(
            {
                "session": session.id,
                "history": artifact(history),
                "source": source,
                "fixed": fixed,
                "observations": captured,
                "cells": cells,
            }
        )
    )
    (tmp_path / "source.duckdb").unlink()
    for path in tmp_path.glob("*.parquet"):
        path.unlink()
    result = subprocess.run(
        [sys.executable, "-m", "tests.history_r76_consumers_worker", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["accepted"] == cells
    evidence = os.getenv("MARIVO_R76_EVIDENCE_DIR")
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("consumers-" + key["id"] + "-" + time["id"] + ".json")).write_text(
            json.dumps({"accepted": cells, "form": time["source_form"]})
        )


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
def test_a10_independent_producer_fixed_continue_and_cold_every_disclosed_k(tmp_path, form):
    receipts = []
    for phase in ("producer", "continue", "cold"):
        result = subprocess.run(
            [sys.executable, "-m", "tests.history_r76_a10_worker", phase, str(tmp_path), form],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        receipts.append(json.loads(result.stdout.splitlines()[-1]))
    assert receipts[1]["K"] > 60
    assert receipts[1]["outputs"] == receipts[2]["outputs"]
    evidence = os.getenv("MARIVO_R76_EVIDENCE_DIR")
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("A10-" + form + ".json")).write_text(
            json.dumps(
                {"receipts": receipts, "manifest": json.loads((tmp_path / "a10.json").read_text())}
            )
        )


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
@pytest.mark.parametrize("history_kind", ["snapshot", "validity"])
def test_checkpoint_axes_use_each_historical_version_and_full_null_tuple(
    tmp_path, form, history_kind
):
    from tests.history_r76_fixtures import historical_history

    history, checkpoints, axis = historical_history(tmp_path, form, history_kind)
    result = history.distribution(at=checkpoints, axes=(axis,)).execute()
    frame = result.to_pandas()
    assert len(frame) == 12
    assert set(frame.loc[frame["checkpoint"] == checkpoints[0], "axis_0"]) == {
        "old",
        "Other",
        "silent",
    }
    assert set(frame.loc[frame["checkpoint"] == checkpoints[1], "axis_0"].dropna()) == {
        "new",
        "silent",
    }
    assert frame.loc[frame["checkpoint"] == checkpoints[1], "axis_0"].isna().sum() == 2
    assert result.known_state_count.execute().to_pandas()["value"].sum() == 3
    retained = result._dataset.verified()
    state = json.loads(
        next(p.table for p in retained.parts if p.role == "history_view")["history_view__retained"][
            0
        ].as_py()
    )
    assert len(state["axes"]) == 8
    assert {row["checkpoint"] for row in state["axes"]} == {
        point.isoformat().replace("+00:00", "Z") for point in checkpoints
    }


@pytest.mark.runtime
def test_zero_duration_transitions_cannot_be_rebuilt_from_interval_rows(tmp_path):
    values = [(0, "started", -1, 1), (0, "paid", 5, 2), (0, "finished", 5, 3)]
    session, members, window, claims, rows = build_lifecycle_public(tmp_path, rows=values)
    history = replay(session, members, window, claims).execute()
    expected = views(rows)
    for name in ("transitions", "intervals", "dwell"):
        assert_view(getattr(history, name)().execute(), name, expected)
    assert history.transitions().execute().count.execute().to_pandas()["value"].sum() == 2
    assert "paid" not in history.intervals().execute().to_pandas()["state"].tolist()


@pytest.mark.runtime
def test_same_positive_intervals_keep_distinct_transition_multiplicity(tmp_path):
    intervals = []
    counts = []
    for pulses in (1, 2):
        values = [
            (0, "started", -1, 1),
            *((0, "pulse", 5, sequence + 2) for sequence in range(pulses)),
            (0, "finished", 10, pulses + 2),
        ]
        root = tmp_path / str(pulses)
        root.mkdir()
        session, members, window, claims, rows = build_lifecycle_public(root, rows=values)
        history = replay(session, members, window, claims).execute()
        expected = views(rows)
        summary = history.transitions().execute()
        instance = history.intervals().execute()
        assert_view(summary, "transitions", expected)
        assert_view(instance, "intervals", expected)
        intervals.append(
            instance.to_pandas()[
                ["state", "start", "end", "observed_duration", "left_clipped", "status"]
            ].to_dict("records")
        )
        frame = summary.to_pandas()
        counts.append(
            frame.loc[
                (frame["from_state"] == "open") & (frame["to_state"] == "open"), "count"
            ].item()
        )
    assert intervals[0] == intervals[1]
    assert counts == [1, 2]


@pytest.mark.runtime
def test_raw_fractional_ticks_round_once_and_summary_means_cannot_be_pooled(tmp_path):
    values = [
        (0, "started", 0, 1),
        (0, "paid", 0.000001, 2),
        (0, "finished", 0.000003, 3),
        (1, "started", 0, 1),
        (1, "paid", 0.000002, 2),
        (1, "finished", 0.000006, 3),
    ]
    session, members, window, claims, rows = build_lifecycle_public(tmp_path, rows=values)
    history = replay(session, members, window, claims).execute()
    expected = views(rows)
    for name in ("intervals", "dwell", "transitions"):
        assert_view(getattr(history, name)().execute(), name, expected)
    dwell = history.dwell().execute()
    means = dwell.to_pandas().set_index("model_state")["mean_duration"]
    assert means["open"] == timedelta(microseconds=2)
    assert means["paid"] == timedelta(microseconds=3)
    for field in (dwell.mean_duration, dwell.median_duration, dwell.p90_duration):
        assert "relation.summarize(method)" not in {a.call for a in field.contract().actions}
        with pytest.raises(AnalysisError, match="cannot be averaged"):
            field.summarize(mv.mean())
    intervals = history.intervals().execute()
    mean = (
        intervals.where(intervals.status.value.eq("completed"))
        .observed_duration.summarize(mv.mean())
        .execute()
    )
    assert mean.to_pandas()["value"].tolist() == [timedelta(microseconds=2)]
    assert mean.rollup().execute().to_pandas()["value"].tolist() == [timedelta(microseconds=2)]


@pytest.mark.runtime
def test_latest_english_chinese_history_examples_execute_identically(tmp_path):
    import re

    from tests.history_r76_fixtures import build_history_public

    session, _, window, completeness, _ = build_history_public(tmp_path)
    snippets = []
    for locale in ("docs", "zh-cn/docs"):
        text = Path(
            "site/src/content/docs" + "/" + locale + "/latest/concepts/analysis-workflow.mdx"
        ).read_text()
        blocks = re.findall(r"```python\n(.*?)```", text, re.S)
        snippets.append(next(block for block in blocks if "checkpoint = window.end" in block))
    assert snippets[0] == snippets[1]
    environment = {
        "mv": mv,
        "ms": ms,
        "session": session,
        "window": window,
        "lifecycle_completeness": completeness,
    }
    history = session.lifecycle.replay(
        ref.state_model("commerce.model"),
        population=session.members(ref.entity("commerce.subjects")),
        window=window,
        seed=mv.from_inception(),
        completeness=completeness,
    )
    environment["history"] = history
    exec(compile(snippets[0], "latest-history-example", "exec"), environment)


@pytest.mark.runtime
@pytest.mark.parametrize("role", ["primary", "history_view", "subject"])
@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_view_receipt_damage_rejects_recovery_continuation_and_exact_hit(tmp_path, role, fault):
    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    logical = replay(session, members, window, claims).execute().intervals()
    fixed = logical.execute()
    reference = artifact(fixed)
    descriptor = fixed._dataset.artifact.descriptor
    receipt = (
        descriptor.primary_receipt.local
        if role == "primary"
        else next(part.local for part in descriptor.parts if part.role == role)
    )
    path = tmp_path / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt History view receipt")
    runs = session.runs().items
    with pytest.raises(AnalysisError):
        session.artifact(reference)
    with pytest.raises(AnalysisError):
        _ = fixed.status
    with pytest.raises(AnalysisError):
        logical.execute()
    assert session.runs().items == runs


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["state", "interval", "violation"])
@pytest.mark.parametrize("form", ["table", "parquet"])
def test_empty_subject_selection_keeps_scope_and_exact_original_components(tmp_path, kind, form):
    from tests.history_r76_fixtures import build_history_public

    session, members, window, claims, _ = build_history_public(
        tmp_path, form=form, subject="c", occurrence="c"
    )
    history = replay(session, members, window, claims)
    if kind == "state":
        relation = history.read(
            mv.in_state(
                ms.model_state(model=ref.state_model("commerce.model"), name="done"), at=END
            )
        )
        chosen = relation.where(
            mv.all_of(relation.value.eq(True), relation.value.eq(False))
        ).members()
    else:
        relation = history.intervals() if kind == "interval" else history.violations()
        field = relation.state if kind == "interval" else relation.kind
        chosen = relation.where(field.value.eq("absent")).members(through=relation.subjects())
    for metric in ("revenue", "fact_count"):
        logical = chosen.observe(
            ref.metric("commerce." + metric),
            during=window,
            via=ref.relationship("commerce.participant"),
        )
        assert [a.call for a in logical.contract().actions] == ["relation.execute()"]
        result = logical.execute()
        assert result.to_pandas().empty
        verified = result._dataset.verified()
        assert (
            verified.contract.signature.domain.binding.scope_id
            == members._node.root.signature.domain.binding.scope_id
        )
        assert next(p.table for p in verified.parts if p.role == "original_state").num_rows == 0
        assert next(p.table for p in verified.parts if p.role == "coverage").num_rows == 0
        assert result.rollup().execute().to_pandas()["value"].tolist() == [0]


@pytest.mark.runtime
def test_source_history_owned_fields_use_registered_row_count(tmp_path):
    session, members, window, claims, _ = build_lifecycle_public(tmp_path)
    history = replay(session, members, window, claims)
    for relation, expected in (
        (history.violations().kind, 3),
        (history.violations().occurred_at, 3),
        (history.intervals().left_clipped, 4),
        (history.intervals().observed_duration, 4),
    ):
        assert relation.summarize(mv.count()).execute().to_pandas()["value"].tolist() == [expected]
