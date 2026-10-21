"""Terminal receipts and manifests remain authoritative after pandas handoff."""

from __future__ import annotations

import json
from copy import deepcopy

import pandas as pd
import pytest

from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.contracts import canonical_json
from tests.analysis.statistics.pandas_workflows import (
    assert_clean,
    daily,
    orders,
    plain,
    receipt_paths,
)
from tests.shared_fixtures import DslCaseFactory, run_ids, terminal_runs


def test_terminal_schema_and_manifest_authority_rejects_damage(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).persist()
    saved = artifact.to_pandas()
    artifact_id = artifact.artifact.id
    store = case.session._runtime.store
    with store._read() as connection:
        payload = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?",
            (artifact_id,),
        ).fetchone()[0]
    body = json.loads(payload)
    assert body["format"] == "marivo.frame_output/v4"
    assert "BoundGraph" not in payload and "fit_inputs" not in payload
    missing_value = deepcopy(body)
    schema = missing_value["value"]["fields"]["data"]["fields"]["schema"]["fields"]
    schema["values"] = schema["values"][:-1]
    foreign_key = deepcopy(body)
    schema = foreign_key["value"]["fields"]["data"]["fields"]["schema"]["fields"]
    schema["axes"][0]["fields"]["columns"][0]["fields"]["slot_id"] = "foreign-key"
    for damaged in (
        {**body, "format": "v999"},
        {**body, "foreign": "unowned"},
        missing_value,
        foreign_key,
    ):
        with store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (canonical_json(damaged), artifact_id),
            )
        try:
            with pytest.raises(AnalysisError) as caught:
                case.session.persisted_frame(artifact_id).to_pandas()
            assert "non-canonical" not in str(caught.value.received)
        finally:
            with store._write() as connection:
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                    (payload, artifact_id),
                )
    pd.testing.assert_frame_equal(case.session.persisted_frame(artifact_id).to_pandas(), saved)


def test_terminal_corruption_rejects_while_exported_copies_are_isolated(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).persist()
    saved = artifact.to_pandas()
    path = receipt_paths(case.root, artifact)[0]
    payload = path.read_bytes()
    before = run_ids(case.session)
    # Editing an exported pandas copy is harmless; changing the persisted output fails.
    altered = plain(artifact)
    altered.loc[:, "value"] = 999
    altered.attrs["axes"][0]["name"] = "changed by caller"
    assert artifact.to_pandas().attrs["axes"][0]["name"] != "changed by caller"
    memory = artifact.compute()
    assert not altered.value.equals(plain(artifact).value)
    for damaged in (payload[:-1], b"damaged terminal data"):
        path.write_bytes(damaged)
        try:
            metadata = case.session.persisted_frame(artifact.artifact.id)
            assert metadata.artifact.id == artifact.artifact.id
            assert metadata.persist() is metadata
            metadata.show(n=0)
            metadata.artifact.show()
            with pytest.raises(AnalysisError):
                metadata.to_pandas()
        finally:
            path.write_bytes(payload)
        assert run_ids(case.session) == before
    pd.testing.assert_frame_equal(artifact.to_pandas(), saved)
    path.unlink()
    try:
        pd.testing.assert_frame_equal(memory.to_pandas(), saved)
        assert artifact.persist() is artifact
        with pytest.raises(AnalysisError):
            artifact.to_pandas()
        assert len(memory.where(memory["value"] > 0).compute().to_pandas()) == 7
    finally:
        path.write_bytes(payload)


@pytest.mark.parametrize("fault", ("missing", "corrupt", "manifest"))
def test_every_saved_receipt_is_required_when_consumed(
    analysis_dsl_case_factory: DslCaseFactory,
    fault: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = daily(case)
    artifact = query.top(3, by=query["value"]).persist()
    saved = artifact.to_pandas()
    paths = receipt_paths(case.root, artifact)
    assert len(paths) == 2  # Primary plus the required frozen calendar directory.
    before = run_ids(case.session)
    for path in paths:
        target = path.parent / "manifest.json" if fault == "manifest" else path
        payload = target.read_bytes()
        if fault == "missing":
            target.unlink()
        else:
            target.write_bytes(b"damaged terminal receipt")
        try:
            reopened = case.session.persisted_frame(artifact.artifact.id)
            reopened.show(n=0)
            reopened.artifact.show()
            assert reopened.persist() is reopened
            with pytest.raises(AnalysisError):
                reopened.show(n=3)
            with pytest.raises(AnalysisError):
                reopened.to_pandas()
            with pytest.raises(AnalysisError):
                reopened.top(0, by=reopened["value"]).compute()
            with pytest.raises(AnalysisError):
                reopened.where(reopened["value"] < 0).compute()
            assert run_ids(case.session) == before
        finally:
            target.write_bytes(payload)
    pd.testing.assert_frame_equal(artifact.to_pandas(), saved)
    assert_clean(case.session, artifacts=1)


@pytest.mark.parametrize("fault", ("cancel", "bad_batch", "before_commit", "native_failure"))
def test_resources_atomic_publication_and_native_failure(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    query = orders(case)
    previous = query.persist()
    saved = previous.to_pandas()
    before = run_ids(case.session)

    def inject(point: str) -> None:
        if point == "before_commit" and fault == "before_commit":
            raise RuntimeError("injected terminal transaction failure")
        if point == "publication_admitted" and fault == "cancel":
            raise KeyboardInterrupt("injected cancellation")

    def fail(*args: object, **kwargs: object) -> pd.DataFrame:
        if fault == "native_failure":
            raise TimeoutError("injected source failure")
        return pd.DataFrame({"unexpected": [1]})

    from marivo.analysis.materialization import terminal_publication

    monkeypatch.setattr(terminal_publication, "_event", inject)
    with monkeypatch.context() as scoped:
        if fault in ("native_failure", "bad_batch"):
            scoped.setattr(SourceSession, "execute", fail)
        with pytest.raises((AnalysisError, KeyboardInterrupt)):
            query.persist()
    pd.testing.assert_frame_equal(previous.to_pandas(), saved)
    assert_clean(case.session, artifacts=1)
    if fault in ("native_failure", "bad_batch"):
        assert run_ids(case.session) == before
    else:
        assert len(run_ids(case.session) - before) == 1
        assert len(terminal_runs(case.session, status="failed")) == 1
