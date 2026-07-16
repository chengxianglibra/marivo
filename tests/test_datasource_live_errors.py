"""Datasource errors expose stable fields and typed recovery actions."""

import inspect

from marivo._authoring.model import AuthoringRepair
from marivo.datasource.errors import (
    DatasourceAuthoringError,
    DatasourceMissingError,
    DatasourceObservedEffects,
)
from marivo.datasource.inspection import _authoring_error
from marivo.introspection.live.model import LiveHelpTarget


def test_datasource_error_exposes_only_stable_recovery_fields() -> None:
    error = DatasourceMissingError(
        message="datasource 'warehouse' is not configured",
        expected="registered project datasource",
        received="warehouse",
        location="models/datasources/",
        repair=AuthoringRepair(
            kind="register",
            help_target=LiveHelpTarget(surface="datasource", canonical_id="register"),
            action="Register the datasource before retrying.",
            snippet=(
                'spec = md.duckdb(name="warehouse", path="warehouse.duckdb")\nmd.register(spec)'
            ),
        ),
    )

    assert not hasattr(error, "details")
    assert not hasattr(error, "hint")
    assert error.repair is not None
    assert error.repair.help_target.surface == "datasource"


def test_authoring_error_preserves_no_query_fact() -> None:
    error = DatasourceAuthoringError(
        code="partition_state_unknown",
        stage="preflight",
        expected="explicit guarded unpruned scope",
        received="partition scope",
        reason="metadata could not prove partition state",
        effect_observed=DatasourceObservedEffects(
            query_executed=False,
            scope_state="unknown",
        ),
        repair=AuthoringRepair(
            kind="rescope",
            help_target=LiveHelpTarget(surface="datasource", canonical_id="unpruned"),
            action="Use an explicit guarded unpruned scope.",
            snippet="md.unpruned(max_rows=1000, timeout_seconds=30)",
            preserves_evidence=True,
        ),
    )

    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is False
    assert error.effect_observed.scope_state == "unknown"


def test_authoring_error_constructor_has_no_legacy_next_calls_bridge() -> None:
    assert "next_calls" not in inspect.signature(_authoring_error).parameters
