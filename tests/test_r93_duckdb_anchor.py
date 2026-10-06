"""Native DuckDB Anchor entry with independent opportunity truth checks."""

from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.datasource.adapters import SourceSession
from tests.json_support import key_json
from tests.r93_source_trace import SourceTrace
from tests.retention_r78_fixtures import build_retention


@pytest.mark.runtime
def test_native_duckdb_anchor_preserves_opportunities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, r93_source_trace: SourceTrace
) -> None:
    monkeypatch.chdir(tmp_path)
    session, anchors, returning, claims = build_retention(tmp_path)
    retained = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    ).execute()
    assert retained.to_pandas().value.tolist() == [True, None, False, None]
    assert retained.to_pandas().cell_tag.tolist() == ["defined", "unknown", "defined", "unknown"]
    assert dict(retained.contract()._facts)["omega_count"] == "4"
    assert dict(retained.contract()._facts)["deterministic_bounds"] == "[0.25,0.75]"
    assert retained._dataset is not None
    retained._dataset.verified()
    descriptor = retained._dataset.artifact.descriptor
    r93_source_trace.physical_keys.extend(
        key_json(item.key)
        for item in descriptor_plan(descriptor, retained._node.definition).physical_requirements
    )
    r93_source_trace.retained.append(
        {
            "kind": type(retained).__name__,
            "verified": True,
            "parts": [
                {"role": part.role, "key_fields": list(part.key_fields)}
                for part in descriptor.parts
            ],
        }
    )

    def forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("Fixed Anchor continuation opened its source")

    monkeypatch.setattr(SourceSession, "batches", forbid)
    for rule, expected in ((mv.any_anchor(), [True, None]), (mv.every_anchor(), [None, False])):
        assert retained.by_subject(rule=rule).execute().to_pandas().value.tolist() == expected
    selected = retained.known_true().execute()
    assert dict(selected.contract()._facts)["omega_count"] == "4"
    members = selected.members(through=selected.subject_binding).execute()
    assert members.to_pandas().member.tolist() == [9007199254740993]
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    r93_source_trace.save(
        "c18-source-duckdb",
        {"backend": "duckdb", "profile": "table"},
        {"opportunities": 4, "window_seconds": 10},
        {
            "values": [True, None, False, None],
            "bounds": [0.25, 0.75],
            "subject_key": 9007199254740993,
            "fixed_source_reads_forbidden": True,
            "resources": 0,
        },
        members,
        (),
    )
