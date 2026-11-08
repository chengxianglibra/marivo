"""The shared terminal Parquet opener preserves sanitized failure boundaries."""

from __future__ import annotations

import pytest

from marivo.analysis.materialization.errors import StorageAccessError
from marivo.analysis.materialization.storage import _open_payload
from tests.shared_fixtures import DslCaseFactory, catalog_get


def test_missing_backing_discards_native_exception_and_locator(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    result = case.session.frame(
        case.session.members(
            catalog_get(case.session.catalog, "entity:sales.customer", kind="entity")
        )
    ).persist()
    receipt = result._local._manifest(result._require_stored("artifact_id")).data.rows.receipt
    path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    path.unlink()
    with pytest.raises(StorageAccessError) as caught:
        _open_payload(case.root, receipt)
    error = caught.value
    assert error.storage_status == "missing"
    assert error.__context__ is None and error.__cause__ is None
