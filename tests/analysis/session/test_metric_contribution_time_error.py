"""Structured missing contribution-time diagnostics at the public boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import NoReturn

import pytest

import marivo
import marivo.analysis as mv
from marivo.datasource.runtime import DatasourceConnectionService
from tests.shared_fixtures import catalog_get, write_minimal_catalog_project


def _fail_read(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("Observation admission must not read the datasource")


def test_missing_contribution_time_is_structured_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_minimal_catalog_project(tmp_path)
    model = tmp_path / "models/semantic/sales/objects.py"
    with model.open("a") as file:
        file.write(
            '\ncustomers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"), '
            'source=md.table("customers"), primary_key=["id"])\n'
            "@ms.metric()\ndef missing_customers():\n    return customers.count()\n"
        )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("missing-times", timezone="UTC")
    metric = catalog_get(session.catalog, "metric:sales.missing_customers", kind="metric")
    customers = catalog_get(session.catalog, "entity:sales.customers", kind="entity")
    fixed_count = session.members(customers).count()
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _fail_read)
    monkeypatch.setattr(DatasourceConnectionService, "source_session", _fail_read)

    # Seven occurrences force bounded text; the typed facts remain complete.
    combined = metric + metric + metric + metric + metric + metric + fixed_count
    for use_axis in (False, True):
        with pytest.raises(mv.MetricContributionTimeError) as captured:
            if use_axis:
                session.observe({"combined": combined}, by=(scope.by(mv.grain("day")),))
            else:
                session.observe({"combined": combined}, where=scope)
        error = captured.value
        assert error.output == "combined"
        assert len(error.missing) == 7
        assert all(item.reason == "no_entity_default" for item in error.missing[:6])
        assert all(item.metric == "metric:sales.missing_customers" for item in error.missing[:6])
        assert all(item.root == "entity:sales.customers" for item in error.missing[:6])
        assert len({item.path for item in error.missing}) == 7
        assert error.missing[-1].reason == "fixed_member_count"
        assert error.missing[-1].root is None
        assert "1 more (see error.missing)" in str(error)
        assert "Entity default_time" in str(error)
        assert "fixed Population.count" in str(error)
        assert error.repair is not None
        assert error.repair.help_target.surface == "semantic"
        with pytest.raises(FrozenInstanceError):
            error.missing[0].root = "other"  # type: ignore[misc]  # Verify immutability.

    # The missing clock does not prevent an ordinary observation.
    session.observe({"combined": combined})
    with pytest.raises(mv.MetricContributionTimeError) as fixed_error:
        session.observe({"fixed": fixed_count}, where=scope)
    assert "fixed Population.count" in str(fixed_error.value)
    assert "Entity default_time" not in str(fixed_error.value)

    for target in (
        "analysis.metric_contribution_time_error",
        "analysis.metric_contribution_time_error.output",
        "analysis.metric_contribution_time_error.missing",
        "analysis.missing_metric_contribution_time",
        "analysis.missing_metric_contribution_time.path",
        "analysis.missing_metric_contribution_time.metric",
        "analysis.missing_metric_contribution_time.root",
        "analysis.missing_metric_contribution_time.reason",
    ):
        marivo.help(target)
    assert "read-only" in capsys.readouterr().out


def test_error_help_discloses_inherited_fields_and_observe_route(
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help(mv.MetricContributionTimeError)
    by_type = capsys.readouterr().out
    marivo.help("analysis.metric_contribution_time_error")
    by_name = capsys.readouterr().out
    assert by_type == by_name
    assert "Kind: error" in by_type
    for field in ("expected", "received", "location", "repair", "output", "missing"):
        assert f"analysis.metric_contribution_time_error.{field}" in by_type
        marivo.help(f"analysis.metric_contribution_time_error.{field}")
        assert "Kind: attribute" in capsys.readouterr().out
    marivo.help("analysis.session.observe")
    observe_help = capsys.readouterr().out
    assert "MetricContributionTimeError" in observe_help
    assert "error.missing" in observe_help
    assert "analysis.metric_contribution_time_error" in observe_help
