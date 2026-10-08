"""Current artifact closed metadata and forecast policy ownership boundaries."""

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.contracts import canonical_json
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
def test_artifact_rejects_unknown_evidence_without_source_replay(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    current = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .execute()
    )
    store, ref = case.session._runtime.store, current.state.artifact_ref
    with store._read() as connection:
        saved = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?", (ref.ref,)
        ).fetchone()[0]
    before = case.session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("unknown metadata recovery tried to replay the source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    try:
        for field in ("unowned_evidence",):
            payload = {**__import__("json").loads(saved), field: {"kind": "unknown"}}
            with store._write() as connection:
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                    (canonical_json(payload), ref.ref),
                )
            for read in (case.session.artifact,):
                with pytest.raises(AnalysisError):
                    read(ref)
                assert case.session.runs().items == before
    finally:
        with store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (saved, ref.ref),
            )
    restored = case.session.artifact(ref)
    assert isinstance(restored, mv.MaterializedNumericRelation)
    assert restored.to_pandas().equals(current.to_pandas())


def test_forecast_model_policies_keep_their_current_owner() -> None:
    from marivo.analysis.forecast_models import drift, naive, periods, seasonal_naive

    assert (
        mv.naive is naive
        and mv.drift is drift
        and mv.periods is periods
        and mv.seasonal_naive is seasonal_naive
    )
