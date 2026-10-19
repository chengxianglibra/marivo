"""Unconfirmed datasource release cannot authorize a successful publication."""

from __future__ import annotations

import pytest

from tests.shared_fixtures import DslCaseFactory


def test_unconfirmed_source_disconnect_prevents_publication(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource._lifetime import BackendLease

    case = analysis_dsl_case_factory("j2")
    logical = case.session.observe({"revenue": case.catalog.metrics.get("sales.revenue")})
    original = BackendLease.close

    def unconfirmed(lease: BackendLease, *, suppress_errors: bool = True) -> bool:
        assert original(lease, suppress_errors=suppress_errors)
        return False

    monkeypatch.setattr(BackendLease, "close", unconfirmed)
    with pytest.raises(MaterializationError) as failed_release:
        logical.persist()
    assert failed_release.value.expected and failed_release.value.received
    assert failed_release.value.repair
    runtime = case.session._runtime
    assert runtime.last_run_ref is None
    assert failed_release.value.run_ref is None
    assert case.session.runs().items == ()
    assert runtime.store.resources(runtime.session_ref) == ()
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
