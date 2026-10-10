"""Independent current-graph state, integrity and offline recovery regressions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.store import SessionStore
from tests.analysis.materialization.state_fixtures import _values
from tests.shared_fixtures import DslCaseFactory


def test_read_factory_never_initializes_missing_or_unversioned_state(tmp_path: Path) -> None:
    for state in ("missing", "empty", "old"):
        root = tmp_path / state
        path = root / ".marivo/analysis/generations/v9/session_store.db"
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
    for value in (logical, fixed, fixed.rollup()):
        contract = value.contract()
        assert contract.actions
        for action in contract.actions:
            route = REGISTRY.resolve(action.help_target.removeprefix("analysis."))
            assert route.descriptor is not None
            method = action.call.split("(", 1)[0].rsplit(".", 1)[-1]
            if hasattr(value, method):
                assert REGISTRY.by_callable(getattr(value, method)) is route.descriptor


def test_retired_standalone_group_artifact_requires_reexecution_without_writes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    category = case.session.members(ms.ref.entity("sales.customer")).read(
        ms.ref.dimension("sales.customer.region")
    )
    for with_key in (False, True):
        # Keep both legacy shapes under the same standalone group-domain refusal.
        domain, _ = category._group_nodes((category,))
        if not with_key:
            domain = category._node.group_domain(())
        dataset = domain.execute()
        reference = dataset.artifact.artifact_ref
        state = case.root / ".marivo/analysis"
        before = {
            p: p.read_bytes()
            for p in state.rglob("*")
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))
        }
        with pytest.raises(IntegrityError) as caught:
            case.session.artifact(reference)
        assert caught.value.stage == "graph_protocol"
        assert "standalone group-domain" in caught.value.received
        assert caught.value.repair is not None
        assert "observe(by=...)" in caught.value.repair.action
        assert {
            p: p.read_bytes()
            for p in state.rglob("*")
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))
        } == before
