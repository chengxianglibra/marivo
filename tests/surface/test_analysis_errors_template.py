"""Structured string template for analysis errors."""

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
        help_target=LiveHelpTarget(surface="analysis", canonical_id="session.time_scope"),
        snippet='session.observe({"value": metric}, during=session.time_scope(start="2026-07-01", end="2026-10-01"))',
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
        '  session.observe({"value": metric}, during=session.time_scope(start="2026-07-01", end="2026-10-01"))'
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
    assert not hasattr(err, "run_ref")
    assert "get_run" not in rendered


def test_error_contract_omits_internal_publication_identity() -> None:
    error = MaterializationError(
        expected="complete input", received="partial input", repair="Restore the input."
    )
    assert not hasattr(error, "run_ref")
    assert "run_ref" not in render_help_text(AnalysisError)[0]
    assert "get_run" not in str(error)


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
