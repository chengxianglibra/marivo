"""Independent current-graph state, integrity and offline recovery regressions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.store import SessionStore
from tests.analysis.materialization.state_fixtures import _values
from tests.shared_fixtures import DslCaseFactory


def test_read_factory_never_initializes_missing_or_unversioned_state(tmp_path: Path) -> None:
    for state in ("missing", "empty", "old"):
        root = tmp_path / state
        path = MaterializationLayout(root).store_db
        if state != "missing":
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as connection:
                if state == "old":
                    connection.execute("PRAGMA user_version=6")
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        with pytest.raises(IntegrityError):
            SessionStore.open_existing(root)
        assert {
            str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
        } == before


def test_current_continuation_registry_resolves_to_actual_bound_methods(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis._capabilities.registry import REGISTRY

    case = analysis_dsl_case_factory("j2")
    logical = _values(case)
    fixed = logical.execute()
    assert len(fixed.to_pandas()) == 4
    for value in (
        logical,
        logical.where(logical["value"] > 0),
        logical.reduce({"total": logical["value"].sum()}),
    ):
        contract = value.contract()
        assert contract.actions
        for action in contract.actions:
            route = REGISTRY.resolve(action.help_target.removeprefix("analysis."))
            assert route.descriptor is not None
            method = action.call.split("(", 1)[0].rsplit(".", 1)[-1]
            if hasattr(value, method):
                assert REGISTRY.by_callable(getattr(value, method)) is route.descriptor


def test_artifact_metadata_rejects_graph_and_continuation_fields_without_writes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.materialization.terminal_protocol import encode

    case = analysis_dsl_case_factory("j1")
    value = _values(case).execute()
    original = encode(value._manifest)
    for field in ("definition_graph", "continuation"):
        payload = json.loads(original)
        payload["value"]["fields"][field] = {"record": "GroupDomain", "fields": {}}
        with case.session._runtime.store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (json.dumps(payload), value.id),
            )
        state = case.root / ".marivo/analysis"
        before = {
            p: p.read_bytes()
            for p in state.rglob("*")
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))
        }
        with pytest.raises(IntegrityError) as caught:
            case.session.artifact(value.id)
        assert caught.value.stage == "terminal_output"
        assert caught.value.repair is not None
        assert "cannot be migrated or replayed" in caught.value.repair.action
        assert {
            p: p.read_bytes()
            for p in state.rglob("*")
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))
        } == before
    with case.session._runtime.store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (original, value.id),
        )
    assert case.session.artifact(value.id).to_pandas()[("value", "value")].tolist() == [
        450,
        150,
        400,
        0,
    ]
