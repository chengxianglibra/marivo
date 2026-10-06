"""A public History Boolean Unknown reaches the fixed cohort consumer."""

import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import TimeScope
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
def test_public_history_unknown_cohort_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_trace: SourceTrace
) -> None:
    monkeypatch.chdir(tmp_path)
    fixture: tuple[
        Session,
        mv.LogicalAnalysisDomain,
        TimeScope,
        tuple[mv.SourceOriginCompletenessDeclarationV1, ...],
        list[tuple[int, str, int, int]],
    ] = build_lifecycle_public(
        tmp_path, backend_name="sqlite", rows=[(0, "started", 0, 1), (0, "finished", 5, 2)]
    )
    session, population, window, claims, _ = fixture
    history = session.lifecycle.replay(
        ms.ref.state_model("commerce.model"),
        population=population,
        window=window,
        seed=mv.from_inception(),
        completeness=(replace(claims[0], complete_through=START + timedelta(seconds=6)),),
    ).execute()
    status = history.read(
        mv.in_state(
            ms.model_state(model=ms.ref.state_model("commerce.model"), name="done"),
            at=START + timedelta(seconds=10),
        )
    ).execute()
    frame = status.to_pandas()
    assert frame.member.tolist() == [9007199254740993, 9007199254740994, 9007199254740995]
    assert frame.cell_tag.tolist() == ["defined", "unknown", "unknown"]
    assert frame.value.tolist() == [True, None, None]
    assert frame.cell_reason.tolist() == [None, "insufficient_coverage", "insufficient_coverage"]
    targets = population.execute()
    native_before = len(source_trace.native_sql)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Fixed cohort must not read the History source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    known = targets.cohort(status.value.is_defined(), rule=mv.any_instance()).execute()
    assert known.to_pandas().member.tolist() == [9007199254740993]
    assert known._dataset is not None
    verified = known._dataset.verified()
    decisions = next(part.table for part in verified.parts if part.role == "cohort_decision")
    assert decisions["cohort_decision__true_count"].to_pylist() == [1, 0, 0]
    assert decisions["cohort_decision__false_count"].to_pylist() == [0, 1, 1]
    assert decisions["cohort_decision__unknown_count"].to_pylist() == [0, 0, 0]
    before_files = set(tmp_path.rglob("*.parquet"))
    errors: list[str] = []
    for rule in (
        mv.any_instance(),
        mv.at_least(1),
        mv.all_instances(empty=mv.empty_opportunity.false()),
    ):
        with pytest.raises(AnalysisError, match="undecidable cohort qualification") as refused:
            targets.cohort(status.value.eq(True), rule=rule).execute()
        errors.append(str(refused.value))
        assert set(tmp_path.rglob("*.parquet")) == before_files
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
    assert len(source_trace.native_sql) == native_before
    assert status.to_pandas().equals(frame)
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "public-history-unknown-cohort.json").write_text(
            json.dumps(
                {
                    "producer": "History.read(in_state(done))",
                    "backend": "sqlite",
                    "cell_tags": frame.cell_tag.tolist(),
                    "known_members": [9007199254740993],
                    "decision_true_counts": [1, 0, 0],
                    "decision_false_counts": [0, 1, 1],
                    "undecidable_errors": errors,
                    "additional_native_submissions": 0,
                    "previous_artifacts_preserved": True,
                    "resources": 0,
                    "scalar_statistical_unknown_verified": False,
                },
                sort_keys=True,
            )
        )
