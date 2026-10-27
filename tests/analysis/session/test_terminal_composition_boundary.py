"""Committed Frames retain data operations and expose audit without a wrapper."""

from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import terminal_runs


def test_artifact_type_and_wrapper_are_removed() -> None:
    assert "Artifact" not in mv.__all__
    assert not hasattr(mv, "Artifact")
    assert not hasattr(mv.AnalysisFrame, "artifact")
    assert not hasattr(mv.AnalysisFrame, "show_audit")
    assert not any(
        name.startswith("Materialized") or name.startswith("Logical") for name in mv.__all__
    )


def test_new_and_reopened_frames_continue_composition_without_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("terminal-boundary")
    query = session.values(by=(), columns={"value": None}, rows=(((), {"value": 7}),))
    saved = query.persist()
    reopened = session.persisted_frame(saved.artifact_id)
    runs = terminal_runs(session)
    sources, catalog = session._sources_value, session._catalog_value
    for result in (saved, reopened):
        with pytest.raises(AnalysisError):
            session.observe({"value": result})  # type: ignore[dict-item]  # Deliberately pass terminal output as Metric input.
        compared = query.compare({"reference": mv.baseline(result)}).compute()
        assert compared.to_pandas()["reference.absolute.value"].tolist() == [0]
        aligned = query.align(result).compute()
        assert aligned.to_pandas()["right.value"].tolist() == [7]
        with pytest.raises(AnalysisError):
            query.select({"value": result})  # type: ignore[dict-item]  # Deliberately pass terminal output as Column input.
    assert terminal_runs(session) == runs
    assert session._sources_value is sources and session._catalog_value is catalog
    assert reopened.to_pandas().equals(saved.to_pandas())

    assert reopened.select({"double": reopened["value"] * 2}).compute().to_pandas()[
        "double"
    ].tolist() == [14]
    assert terminal_runs(session) == runs
