"""Native DuckDB History admission with independent original trace checks."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.datasource.adapters import SourceSession
from scripts import r9_qualification_requirements as freeze
from scripts.r82_deviation_requirements import key_json
from tests.lifecycle_r75_fixtures import END, START, TRIGGERS, build_lifecycle_public
from tests.r93_source_trace import SourceTrace


@pytest.mark.runtime
def test_native_duckdb_history_preserves_original_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, r93_source_trace: SourceTrace
) -> None:
    monkeypatch.chdir(tmp_path)
    rows = [(0, "started", 0, 1), (0, "paid", 5, 2), (0, "finished", 10, 3)]
    build_lifecycle_public(tmp_path, backend_name="duckdb", rows=rows)
    session = mv.session.get_or_create("r93-duckdb-history", report_timezone="UTC")
    claims = (
        mv.SourceOriginCompletenessDeclarationV1(
            inputs=tuple(ms.ref.event("commerce." + event) for event in TRIGGERS),
            source_origin_ref=ms.ref.datasource("warehouse"),
            complete_through=END,
            rationale="Complete original source facts through the report end.",
        ),
    )
    logical = session.lifecycle.replay(
        ms.ref.state_model("commerce.model"),
        population=session.members(ms.ref.entity("commerce.subjects")),
        window=mv.time_scope(start=START, end=END),
        seed=mv.from_inception(),
        completeness=claims,
    )
    history = logical.execute()
    assert history._dataset is not None
    verified = history._dataset.verified()
    records = [
        freeze.obj(freeze.checked(json.loads(row["history__record"])))
        for row in verified.parts[0].table.to_pylist()
    ]
    assert [record["subject"] for record in records] == [
        [9007199254740993],
        [9007199254740994],
        [9007199254740995],
    ]
    assert [record["classification"] for record in records] == [
        "seeded",
        "not_started",
        "not_started",
    ]
    trace = [freeze.obj(item) for item in freeze.arr(records[0]["evaluations"])]
    assert [item["disposition"] for item in trace] == [
        "inception",
        "legal_transition",
        "legal_transition",
    ]
    assert [item["after"] for item in trace] == ["open", "paid", "done"]
    occurrences = [freeze.obj(item["occurrence"]) for item in trace]
    assert [item["key"] for item in occurrences] == [
        [9007199254740993],
        [9007199254740994],
        [9007199254740995],
    ]
    assert [item["occurred_at"] for item in occurrences] == [
        (START + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")
        for offset in (0, 5, 10)
    ]
    assert len(freeze.arr(records[0]["transitions"])) == 2
    assert records[0]["violations"] == []
    assert all(record["evaluations"] == [] for record in records[1:])
    descriptor = history._dataset.artifact.descriptor
    r93_source_trace.physical_keys.extend(
        key_json(item.key)
        for item in descriptor_plan(descriptor, logical._node.definition).physical_requirements
    )
    r93_source_trace.retained.append(
        {
            "kind": type(history).__name__,
            "verified": True,
            "parts": [
                {"role": part.role, "key_fields": list(part.key_fields)}
                for part in descriptor.parts
            ],
        }
    )

    def forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("Fixed History continuation opened its source")

    monkeypatch.setattr(SourceSession, "batches", forbid)
    total = history.transitions().count.summarize(mv.sum()).execute()
    assert total.to_pandas().value.tolist() == [2]
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    r93_source_trace.save(
        "c13-source-duckdb",
        {"backend": "duckdb", "profile": "table"},
        {"rows": rows, "complete_through": END.isoformat()},
        {
            "subject_keys": [9007199254740993, 9007199254740994, 9007199254740995],
            "states": ["open", "paid", "done"],
            "transition_count": 2,
            "fixed_source_reads_forbidden": True,
            "resources": 0,
        },
        total,
        (),
    )
