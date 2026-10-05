"""Probe the existing public Duration quotient as a scalar Unknown producer."""

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.datasource.adapters import SourceSession
from tests.lifecycle_r75_fixtures import END, START, TRIGGERS, build_lifecycle_public


@pytest.mark.runtime
def test_public_duration_quotient_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    build_lifecycle_public(tmp_path, backend_name="sqlite")
    session = mv.session.get_or_create("unknown-ratio", report_timezone="UTC")
    claims = (
        mv.SourceOriginCompletenessDeclarationV1(
            inputs=tuple(ms.ref.event("commerce." + event) for event in TRIGGERS),
            source_origin_ref=ms.ref.datasource("warehouse"),
            complete_through=START - timedelta(seconds=1),
            rationale="Known inception with incomplete observation coverage.",
        ),
    )
    history = session.lifecycle.replay(
        ms.ref.state_model("commerce.model"),
        population=session.members(ms.ref.entity("commerce.subjects")),
        window=mv.time_scope(start=START, end=END),
        seed=mv.from_inception(),
        completeness=claims,
    ).execute()
    observed = history.intervals().observed_duration.execute()
    assert "unknown" in set(observed.to_pandas().cell_tag)
    before = set(tmp_path.rglob("*.parquet"))

    def forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("Retained Duration ratio opened its source")

    monkeypatch.setattr(SourceSession, "batches", forbid)
    with pytest.raises(DatasetConstructionError) as refused:
        observed.ratio(observed)
    assert refused.value.expected == "a retained operand error envelope"
    assert refused.value.received == (
        "History owned fields currently qualify selection and current-row statistics "
        "without comparison endpoints"
    )
    assert set(tmp_path.rglob("*.parquet")) == before
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "history-duration-ratio-refusal.json").write_text(
            json.dumps(
                {
                    "producer": "history.intervals().observed_duration",
                    "tags": observed.to_pandas().cell_tag.tolist(),
                    "expected": refused.value.expected,
                    "received": refused.value.received,
                    "fixed_source_reads_forbidden": True,
                    "no_partial_publication": True,
                    "resources": 0,
                    "scalar_numeric_unknown_runtime_verified": False,
                },
                sort_keys=True,
            )
        )
