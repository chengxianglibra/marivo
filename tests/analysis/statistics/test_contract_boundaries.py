"""Artifact metadata is closed; statistical policy choices belong to pandas."""

import json

import pandas as pd
import pytest

from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.contracts import canonical_json
from tests.analysis.statistics.pandas_workflows import ForecastModel, forecast, orders
from tests.shared_fixtures import DslCaseFactory, run_ids


def test_artifact_rejects_unknown_evidence_without_source_replay(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.statistics.deviation_oracle import forbidden

    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).persist()
    store, ref = case.session._runtime.store, artifact.artifact.id
    with store._read() as connection:
        saved = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?", (ref,)
        ).fetchone()[0]
    before = run_ids(case.session)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    payload = {**json.loads(saved), "unowned_evidence": {"kind": "unknown"}}
    with store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (canonical_json(payload), ref),
        )
    try:
        with pytest.raises(AnalysisError) as caught:
            case.session.persisted_frame(ref)
        assert "non-canonical" not in str(caught.value.received)
        assert run_ids(case.session) == before
    finally:
        with store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (saved, ref),
            )
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(ref).to_pandas(), artifact.to_pandas()
    )


def test_forecast_model_policies_keep_their_current_owner() -> None:
    values = pd.Series([1, 2, 7])
    models: tuple[tuple[ForecastModel, list[int]], ...] = (
        ("naive", [7, 7]),
        ("drift", [10, 13]),
        ("seasonal_naive", [2, 7]),
    )
    for model, wanted in models:
        assert forecast(values, model).prediction.tolist() == wanted
