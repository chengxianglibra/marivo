"""Unconfirmed datasource release cannot authorize a successful publication."""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory


def test_unconfirmed_source_disconnect_prevents_publication(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.datasets.errors import DatasetConstructionError
    from marivo.datasource._lifetime import BackendLease

    case = analysis_dsl_case_factory("j2")
    logical = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .deviation(method="zscore")
    )
    original = BackendLease.close

    def unconfirmed(lease: BackendLease, *, suppress_errors: bool = True) -> bool:
        assert original(lease, suppress_errors=suppress_errors)
        return False

    monkeypatch.setattr(BackendLease, "close", unconfirmed)
    with pytest.raises(DatasetConstructionError) as failed_release:
        logical.execute()
    assert failed_release.value.expected == "confirmed source connection release"
    assert failed_release.value.received == "selected backend disconnect failed or unavailable"
    assert failed_release.value.repair is not None
    runtime = case.session._runtime
    assert runtime.last_run_ref is not None
    assert failed_release.value.run_ref == runtime.last_run_ref
    run = runtime.store._graph_run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert runtime.store.resources(runtime.session_ref) == ()
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
