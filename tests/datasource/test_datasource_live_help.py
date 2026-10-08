"""Datasource live-help target resolution and runtime behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import marivo
import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo._help.model import MarivoHelpTargetError
from marivo.datasource.catalog import DatasourceCatalog
from marivo.datasource.errors import DatasourceMissingError
from marivo.datasource.inspection import (
    ExecutionCapabilities,
    Partitioning,
    PhysicalExtent,
    SourceInspection,
)
from marivo.datasource.snapshot import DiscoverySnapshot, SnapshotCoverage
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget


@pytest.mark.parametrize(
    ("target", "canonical_id"),
    [
        ("inspect", "inspect"),
        (md.inspect, "inspect"),
        (md.SourceInspection.sample, "SourceInspection.sample"),
        (md.SourceInspection.source_column, "SourceInspection.source_column"),
        (md.SourceInspection, "SourceInspection"),
        (DatasourceMissingError, "DatasourceMissingError"),
    ],
)
def test_help_resolves_supported_target_kinds(target: object, canonical_id: str) -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    resolved = resolve_live_target(target, DATASOURCE_LIVE_SURFACE)
    assert canonical_id in {
        resolved.canonical_id,
        resolved.type_name,
        resolved.error_name,
    }


@pytest.mark.parametrize(
    "target",
    (
        "SourceInspection.sample",
        "inspection.sample",
        "md.SourceInspection.sample",
        "md.inspection.sample",
    ),
)
def test_registered_sample_string_paths_resolve_to_one_descriptor(target: str) -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    resolved = resolve_live_target(target, DATASOURCE_LIVE_SURFACE)
    assert resolved.kind == "descriptor"
    assert resolved.canonical_id == "SourceInspection.sample"


def test_inspection_source_column_help_routes_to_exact_method() -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    for target in (
        "SourceInspection.source_column",
        "inspection.source_column",
        md.SourceInspection.source_column,
    ):
        resolved = resolve_live_target(target, DATASOURCE_LIVE_SURFACE)
        assert resolved.canonical_id == "SourceInspection.source_column"


def test_unknown_string_raises_typed_bounded_error() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        marivo.help("inspekt")

    assert len(exc_info.value.candidates) <= SURFACE_LIMITS.help_suggestion_limit
    assert "datasource.inspect" in exc_info.value.candidates


def test_register_help_discloses_the_declaration_repair(capsys: pytest.CaptureFixture[str]) -> None:
    marivo.help("datasource.register")
    output = capsys.readouterr().out
    assert "datasource_register_outside_loader" in output
    assert "execution outside model loading" in output
    assert "datasource constructor directly" in output
    assert "md.duckdb(name=" in output


@pytest.fixture
def datasource_runtime_targets(tmp_path: Path) -> tuple[object, ...]:
    source = md.table("orders")
    ref = ms.ref.datasource("warehouse")
    scope = md.unpruned(max_rows=10, timeout_seconds=5)
    inspection = SourceInspection(
        datasource=ref,
        source=source,
        physical_extent=PhysicalExtent(None, "unknown", None, "unknown", "metadata", ()),
        partitioning=Partitioning("none", (), None, (), True, False),
        execution_capabilities=ExecutionCapabilities(True, False, True, False),
        schema=(),
        warnings=(),
        _project_root=tmp_path,
    )
    snapshot = DiscoverySnapshot(
        id="snapshot-1",
        datasource=ref,
        source=source,
        scope=scope,
        columns=(),
        schema_fingerprint="schema-1",
        profiles=(),
        coverage=SnapshotCoverage(0, 0, "exhaustive", "scope_exact", "first_rows_limit", ()),
        persist_values=False,
        value_evidence_state="value_evidence_unavailable",
        cache_status="fresh",
        created_at=datetime.now(),
        expires_at=datetime.now(),
        _project_root=tmp_path,
    )
    return (
        md.duckdb(name="warehouse"),
        DatasourceCatalog(workspace_dir=tmp_path),
        source,
        scope,
        inspection,
        snapshot,
        DatasourceMissingError(message="warehouse is missing"),
    )


def test_runtime_help_accepts_only_registered_datasource_instances(
    datasource_runtime_targets: tuple[object, ...],
) -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    for target in datasource_runtime_targets:
        resolved = resolve_live_target(target, DATASOURCE_LIVE_SURFACE)
        assert resolved.canonical_id or resolved.type_name or resolved.error_name


def test_error_help_kind_depends_on_concrete_repair_target() -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    with_repair = DatasourceMissingError(
        message="warehouse is missing",
        expected="registered datasource",
        received="warehouse",
        location="datasource catalog",
        repair=AuthoringRepair(
            kind="inspect",
            help_target=LiveHelpTarget(surface="semantic"),
            action="Inspect semantic help before continuing.",
            snippet="marivo.help()",
            candidates=("load",),
        ),
    )
    without_repair = DatasourceMissingError(message="warehouse is missing")

    briefing = resolve_live_target(with_repair, DATASOURCE_LIVE_SURFACE)
    contract = resolve_live_target(without_repair, DATASOURCE_LIVE_SURFACE)
    error_class = resolve_live_target(DatasourceMissingError, DATASOURCE_LIVE_SURFACE)

    assert briefing.kind == "error_briefing"
    assert contract.kind == "error_contract"
    assert error_class.kind == "error_contract"
    assert contract == error_class
    assert with_repair.repair is not None
    assert with_repair.repair.help_target == LiveHelpTarget(surface="semantic")


def test_live_help_performs_no_datasource_effects(
    datasource_runtime_targets: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform datasource effects")

    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend_with_secrets", fail)
    monkeypatch.setattr("marivo.datasource.authoring_store.AuthoringStore.write_snapshot", fail)
    monkeypatch.setattr("marivo.config.load_project_config", fail)

    assert marivo.help() is None
    for target in ("inspect", md.SourceInspection, *datasource_runtime_targets):
        assert marivo.help(target) is None
