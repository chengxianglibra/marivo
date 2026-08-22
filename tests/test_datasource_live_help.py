"""Datasource live-help target and render contracts."""

from __future__ import annotations

import sys
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
from tests.shared_fixtures import rendered_help


def _text(target: object | None = None) -> str:
    return rendered_help(target, owner="datasource")


@pytest.mark.parametrize(
    ("target", "canonical_id"),
    [
        ("inspect", "inspect"),
        (md.inspect, "inspect"),
        (md.SourceInspection.sample, "SourceInspection.sample"),
        (md.SourceInspection, "SourceInspection"),
        (DatasourceMissingError, "DatasourceMissingError"),
    ],
)
def test_help_resolves_supported_target_kinds(target: object, canonical_id: str) -> None:
    text = _text(target)
    assert canonical_id in text


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
    assert _text(target).startswith("SourceInspection.sample\n")


def test_unknown_string_raises_typed_bounded_error() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        _text("inspekt")

    assert len(exc_info.value.candidates) <= SURFACE_LIMITS.help_suggestion_limit
    assert "datasource.inspect" in exc_info.value.candidates


def test_root_help_reveals_current_environment() -> None:
    text = _text()

    assert f"Marivo: {marivo.__version__}" in text
    assert f"Python: {sys.executable}" in text
    assert f"Package: {Path(marivo.__file__).resolve()}" in text


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
    for target in datasource_runtime_targets:
        text = _text(target)
        assert text.strip()
        if isinstance(target, DiscoverySnapshot):
            assert "Public consumption: contract, show, render" in text
            assert "call .contract().show()" in text


def test_sample_help_teaches_snapshot_contract_and_named_rows() -> None:
    text = _text("SourceInspection.sample")

    assert "snapshot.contract().show()" in text
    assert "persist_values=True" in text
    assert 'snapshot.retained_values[0]["order_id"]' in text


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
    assert _text(without_repair) == _text(DatasourceMissingError)
    text = _text(with_repair)
    assert "Kind: inspect" in text
    assert "Expected: registered datasource" in text
    assert "Received: warehouse" in text
    assert "Location: datasource catalog" in text
    assert "Next help: marivo.help()" in text
    assert "ms.help" not in text
    assert "Snippet:" in text
    assert "Candidates: load" in text


def test_live_help_performs_no_datasource_effects(
    datasource_runtime_targets: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform datasource effects")

    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend_with_secrets", fail)
    monkeypatch.setattr("marivo.datasource.authoring_store.AuthoringStore.write_snapshot", fail)
    monkeypatch.setattr("marivo.config.load_project_config", fail)

    assert _text()
    for target in ("inspect", md.SourceInspection, *datasource_runtime_targets):
        assert _text(target)
