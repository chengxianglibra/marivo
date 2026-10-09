"""Structured string template for analysis errors."""

import pytest

from marivo._help.render import render_help_text
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.errors import (
    DatasourceEnvVarMissingError,
    DatasourceSecretStorePermissionsError,
)
from marivo.introspection.live.model import LiveHelpTarget


def test_analysis_error_renders_stable_fields_and_repair() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Pass a parseable time_scope.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        snippet='members.observe(metric, during=mv.time_scope(start="2026-07-01", end="2026-10-01"))',
    )
    err = AnalysisError(
        message="something happened",
        expected="a parseable absolute time_scope",
        received="param a was invalid",
        location="session.compare call",
        repair=repair,
        hint="try fixing X",
    )

    rendered = str(err)

    assert rendered.startswith("AnalysisError: something happened")
    assert "Location: session.compare call" in rendered
    assert "Expected: a parseable absolute time_scope" in rendered
    assert "Received: param a was invalid" in rendered
    assert "Hint: try fixing X" in rendered
    assert "Repair:" in rendered
    assert "  Pass a parseable time_scope." in rendered
    assert (
        '  members.observe(metric, during=mv.time_scope(start="2026-07-01", end="2026-10-01"))'
        in rendered
    )


def test_base_template_omits_missing_optional_sections() -> None:
    err = AnalysisError(message="something happened")

    rendered = str(err)

    assert rendered == "AnalysisError: something happened"
    assert "Location:" not in rendered
    assert "Expected:" not in rendered
    assert "Received:" not in rendered
    assert "Hint:" not in rendered
    assert "Repair:" not in rendered
    assert err.run_ref is None
    assert "get_run" not in rendered


@pytest.mark.parametrize("with_repair", (False, True))
def test_error_and_live_help_share_exact_run_inspection(with_repair: bool) -> None:
    error = (
        MaterializationError(
            expected="complete input",
            received="partial input",
            repair="Restore the input.",
            run_ref="run_exact",
        )
        if with_repair
        else AnalysisError(message="execution failed", run_ref="run_exact")
    )
    for rendered in (str(error), render_help_text(error)[0]):
        assert "Run: run_exact" in rendered
        assert "session.get_run('run_exact').show()" in rendered
        assert "marivo.help('analysis.session.get_run')" in rendered
    assert "optional run_ref" in render_help_text(AnalysisError)[0]


def test_datasource_env_var_missing_mentions_cache_and_validation() -> None:
    err = DatasourceEnvVarMissingError(
        message="secret missing",
        expected="an exported secret environment variable",
        received="TRINO_PASSWORD",
        location="datasource 'wh' field 'password'",
        repair=__import__("marivo.datasource.errors", fromlist=["repair"]).repair(
            kind="environment",
            canonical_id="test",
            action="Export the variable and validate the datasource.",
            snippet='md.test("wh")',
        ),
    )

    rendered = str(err)

    assert "TRINO_PASSWORD" in rendered
    assert "Expected: an exported secret environment variable" in rendered
    assert 'md.test("wh")' in rendered


def test_secret_store_permissions_error_has_chmod_fix() -> None:
    err = DatasourceSecretStorePermissionsError(
        message="secret store is too open",
        expected="owner-only secret-store permissions",
        received="0o644",
        location="/Users/alice/.marivo/secrets.toml",
        repair=__import__("marivo.datasource.errors", fromlist=["repair"]).repair(
            kind="environment",
            canonical_id="test",
            action="Restrict the secret store.",
            snippet="chmod 600 ~/.marivo/secrets.toml",
        ),
    )

    rendered = str(err)

    assert "Location: /Users/alice/.marivo/secrets.toml" in rendered
    assert "0o644" in rendered
    assert "chmod 600 ~/.marivo/secrets.toml" in rendered
