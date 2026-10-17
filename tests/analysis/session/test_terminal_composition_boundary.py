"""Committed output cannot become a DSL receiver or computation dependency."""

from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.public_dsl import LogicalNumericRelation
from tests.shared_fixtures import numeric_literal_node, numeric_program_query


@pytest.mark.parametrize(
    "result_type",
    [
        getattr(mv, name)
        for name in mv.__all__
        if name.startswith("Materialized") and name != "MaterializedDatasetState"
    ],
)
def test_legacy_result_types_expose_only_output_reads(result_type: type) -> None:
    methods = {
        name
        for name in dir(result_type)
        if not name.startswith("_") and callable(getattr(result_type, name))
    }
    assert methods <= {"show", "to_pandas", "findings", "finding", "evidence_digest"}
    assert {"show", "to_pandas"} <= methods
    for field in ("value", "subject_binding", "absolute", "score", "members", "contract"):
        assert not hasattr(result_type, field)


def test_new_reopened_and_legacy_results_cannot_enter_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("terminal-boundary")
    saved = numeric_program_query(session, {"value": numeric_literal_node(7)}).execute()
    reopened = session.artifact(saved.id)
    # Rejected input kinds must be checked before inspecting a receiver's graph.
    logical = object.__new__(LogicalNumericRelation)
    legacy = object.__new__(mv.MaterializedNumericRelation)
    runs = session.runs().items
    for result in (saved, reopened, legacy):
        with pytest.raises(AnalysisError):
            session.observe({"value": result})
        with pytest.raises(AnalysisError):
            logical.compare(result)
        with pytest.raises(AnalysisError):
            logical.ratio(result)
        with pytest.raises(AnalysisError):
            logical.share_of(result)
        with pytest.raises(AnalysisError):
            mv.table(value=result)
        with pytest.raises(AnalysisError):
            mv.one_to_one(left=logical, right=result, via=ms.ref.relationship("sales.link"))
        with pytest.raises(AnalysisError):
            mv.reference_weights(result, strata=(), unit=ms.ref.entity("sales.customer"))
    assert session.runs().items == runs
    assert session._sources_value is None and session._catalog_value is None
    assert reopened.to_pandas().equals(saved.to_pandas())
