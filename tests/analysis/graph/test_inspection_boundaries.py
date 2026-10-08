"""Current graph inspection preserves authority and sanitized read boundaries."""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo.analysis.materialization import graph_storage
from marivo.analysis.materialization.errors import StorageAccessError
from marivo.analysis.materialization.store import SessionStore
from tests.shared_fixtures import DslCaseFactory


def _snapshot(store: SessionStore) -> str:
    with store._read() as connection:
        return "\n".join(connection.iterdump())


def test_missing_backing_discards_native_exception_and_locator(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    result = case.session.members(ms.ref.entity("sales.customer")).execute()
    assert result._dataset is not None
    receipt = result._dataset.artifact.descriptor.primary_receipt.local
    path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    path.unlink()
    store = case.session._runtime.store
    before = _snapshot(store)
    with pytest.raises(StorageAccessError) as caught:
        graph_storage.read_table(case.root, receipt)
    error = caught.value
    assert error.storage_status == "missing"
    assert error.__context__ is None and error.__cause__ is None
    assert str(path) not in str(error) and str(path) not in repr(error)
    assert _snapshot(store) == before
