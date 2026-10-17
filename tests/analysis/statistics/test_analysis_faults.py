"""Terminal receipts and manifests remain authoritative after pandas handoff."""

from __future__ import annotations

import json
from dataclasses import replace

import pandas as pd
import pytest

from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.terminal_storage import read_output
from tests.analysis.statistics.pandas_workflows import assert_clean, orders, plain, receipt_paths
from tests.shared_fixtures import DslCaseFactory, run_ids


def test_terminal_schema_and_manifest_authority_rejects_damage(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).execute()
    saved = artifact.to_pandas()
    data = artifact._manifest.data
    # Terminal output freezes schema and receipt; source/fit graphs are not saved.
    with pytest.raises(AnalysisError):
        read_output(case.root, replace(data.schema, values=data.schema.values[:-1]), data.rows)
    with pytest.raises(AnalysisError):
        read_output(
            case.root,
            replace(
                data.schema,
                axes=(
                    replace(
                        data.schema.axes[0],
                        columns=(replace(data.schema.axes[0].columns[0], slot_id="foreign-key"),),
                    ),
                ),
            ),
            data.rows,
        )
    store = case.session._runtime.store
    with store._read() as connection:
        payload = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?", (artifact.id,)
        ).fetchone()[0]
    body = json.loads(payload)
    assert body["format"] == "marivo.terminal_output/v1"
    assert "BoundGraph" not in payload and "fit_inputs" not in payload
    for field, value in (("format", "v999"), ("foreign", "unowned")):
        damaged = {**body, field: value}
        with store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (json.dumps(damaged), artifact.id),
            )
        try:
            with pytest.raises(AnalysisError):
                case.session.artifact(artifact.id)
        finally:
            with store._write() as connection:
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                    (payload, artifact.id),
                )
    pd.testing.assert_frame_equal(case.session.artifact(artifact.id).to_pandas(), saved)


def test_terminal_corruption_rejects_while_exported_copies_are_isolated(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).execute()
    saved = artifact.to_pandas()
    path = receipt_paths(case.root, artifact)[0]
    payload = path.read_bytes()
    before = run_ids(case.session)
    # Editing an exported pandas copy is harmless; changing the persisted output fails.
    altered = plain(artifact)
    altered.loc[:, "value"] = 999
    assert not altered.value.equals(plain(artifact).value)
    for damaged in (payload[:-1], b"damaged terminal data"):
        path.write_bytes(damaged)
        try:
            metadata = case.session.artifact(artifact.id)
            assert metadata.id == artifact.id
            with pytest.raises(AnalysisError):
                metadata.to_pandas()
        finally:
            path.write_bytes(payload)
        assert run_ids(case.session) == before
    pd.testing.assert_frame_equal(artifact.to_pandas(), saved)


@pytest.mark.parametrize("fault", ("missing", "corrupt", "manifest"))
def test_every_saved_receipt_is_required_when_consumed(
    analysis_dsl_case_factory: DslCaseFactory,
    fault: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = orders(case)
    artifact = query.top(3, by=query["value"]).execute()
    saved = artifact.to_pandas()
    paths = receipt_paths(case.root, artifact)
    assert len(paths) >= 2  # Primary plus explicit saved display-order output.
    before = run_ids(case.session)
    for path in paths:
        target = path.parent / "manifest.json" if fault == "manifest" else path
        payload = target.read_bytes()
        if fault == "missing":
            target.unlink()
        else:
            target.write_bytes(b"damaged terminal receipt")
        try:
            reopened = case.session.artifact(artifact.id)
            with pytest.raises(AnalysisError):
                reopened.show(n=3)
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
    previous = query.execute()
    saved = previous.to_pandas()
    runtime = case.session._runtime

    def inject(point: str) -> None:
        if point == "before_commit" and fault == "before_commit":
            raise RuntimeError("injected terminal transaction failure")
        if point == "query_admitted" and fault == "cancel":
            raise KeyboardInterrupt("injected cancellation")

    def fail(*args: object, **kwargs: object) -> pd.DataFrame:
        if fault == "native_failure":
            raise TimeoutError("injected source failure")
        return pd.DataFrame({"unexpected": [1]})

    runtime._hook = inject
    try:
        with monkeypatch.context() as scoped:
            if fault in ("native_failure", "bad_batch"):
                scoped.setattr(SourceSession, "execute", fail)
            with pytest.raises((AnalysisError, KeyboardInterrupt)):
                query.execute()
    finally:
        runtime._hook = None
    pd.testing.assert_frame_equal(previous.to_pandas(), saved)
    assert_clean(case.session, artifacts=1)
