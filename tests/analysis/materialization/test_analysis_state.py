"""Independent current-graph state, integrity and offline recovery regressions."""

from __future__ import annotations

import json

import pytest

from marivo.analysis.materialization.errors import IntegrityError
from tests.analysis.materialization.state_fixtures import _values
from tests.shared_fixtures import DslCaseFactory, terminal_runs


def test_current_continuation_registry_resolves_to_actual_bound_methods(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis._capabilities.registry import REGISTRY

    case = analysis_dsl_case_factory("j2")
    logical = _values(case)
    fixed = logical.compute()
    assert len(fixed.to_pandas()) == 4
    for value in (
        logical,
        logical.where(logical["value"] > 0),
        logical.reduce({"total": logical["value"].sum()}),
    ):
        for method in ("select", "where", "compute", "persist", "show"):
            route = REGISTRY.resolve(f"analysis_frame.{method}")
            assert route.descriptor is not None
            assert REGISTRY.by_callable(getattr(value, method)) is route.descriptor


def test_artifact_metadata_rejects_graph_and_continuation_fields_without_writes(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization.terminal_protocol import encode

    case = analysis_dsl_case_factory("j1")
    saved = _values(case).persist()
    value = saved
    original = encode(value._local._manifest(value._require_stored("artifact_id")))
    for field in ("definition_graph", "continuation"):
        payload = json.loads(original)
        payload["value"]["fields"][field] = {"record": "GroupDomain", "fields": {}}
        with case.session._runtime.store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (json.dumps(payload), value.artifact_id),
            )
        state = case.root / ".marivo/analysis"
        before = {
            p: p.read_bytes()
            for p in state.rglob("*")
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))
        }
        with pytest.raises(IntegrityError) as caught:
            case.session.persisted_frame(value.artifact_id)
        assert caught.value.stage == "terminal_output"
        assert caught.value.repair is not None
        assert "cannot be migrated or replayed" in caught.value.repair.action
    with case.session._runtime.store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (original, value.artifact_id),
        )
    import pyarrow.parquet as pq

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Artifact audit opened result payloads")

    with monkeypatch.context() as audit_only:
        audit_only.setattr(pq, "ParquetFile", forbidden)
        assert case.session.persisted_frame(value.artifact_id).artifact_id == value.artifact_id
    assert len(terminal_runs(case.session)) == 1
    assert case.session.persisted_frame(value.artifact_id).to_pandas()["value"].tolist() == [
        450,
        150,
        400,
        0,
    ]
