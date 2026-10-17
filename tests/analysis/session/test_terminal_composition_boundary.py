"""Committed output cannot become a DSL receiver or computation dependency."""

from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError


def test_artifact_exposes_only_terminal_output_reads() -> None:
    methods = {
        name
        for name in dir(mv.Artifact)
        if not name.startswith("_") and callable(getattr(mv.Artifact, name))
    }
    assert methods == {"show", "to_pandas", "findings", "evidence_digest"}
    for field in (
        "value",
        "population",
        "select",
        "where",
        "top",
        "reduce",
        "rollup",
        "compare",
        "align",
        "drill",
        "share",
        "contract",
        "execute",
    ):
        assert not hasattr(mv.Artifact, field)
    assert not any(
        name.startswith("Materialized") or name.startswith("Logical") for name in mv.__all__
    )


def test_new_and_reopened_artifacts_cannot_enter_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("terminal-boundary")
    query = session.values(by=(), columns={"value": None}, rows=(((), {"value": 7}),))
    saved = query.execute()
    reopened = session.artifact(saved.id)
    runs = session.runs().items
    sources, catalog = session._sources_value, session._catalog_value
    for result in (saved, reopened):
        with pytest.raises(AnalysisError):
            session.observe({"value": result})  # type: ignore[dict-item]  # Deliberately pass terminal output as Metric input.
        with pytest.raises(AnalysisError):
            query.compare(result)  # type: ignore[arg-type]  # Deliberately pass terminal output as Query input.
        with pytest.raises(AnalysisError):
            query.align(result)  # type: ignore[arg-type]  # Deliberately pass terminal output as Query input.
        with pytest.raises(AnalysisError):
            query.select({"value": result})  # type: ignore[dict-item]  # Deliberately pass terminal output as Column input.
        with pytest.raises(AnalysisError):
            mv.broadcast(query, to=result)  # type: ignore[arg-type]  # Deliberately pass terminal output as Query input.
    assert session.runs().items == runs
    assert session._sources_value is sources and session._catalog_value is catalog
    assert reopened.to_pandas().equals(saved.to_pandas())
