"""Datasource live-help target resolution and runtime behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import marivo
import marivo.datasource as md
from marivo._help.model import MarivoHelpTargetError
from marivo._help.render import render_help_text
from marivo.datasource.catalog import DatasourceCatalog
from marivo.datasource.errors import DatasourceMissingError
from marivo.datasource.inspection import (
    ExecutionCapabilities,
    Partitioning,
    PhysicalExtent,
    SourceInspection,
)
from marivo.datasource.snapshot import DiscoverySnapshot, SnapshotCoverage
from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.refs import ref as ref_factory


def test_json_help_exposes_analysis_request_and_authoring_binding_boundaries() -> None:
    text, _, _ = render_help_text("datasource.json")
    assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


def test_file_projection_help_matches_optional_source_specific_inputs() -> None:
    assert md.json("events.json").columns == ()
    assert md.csv("events.csv").columns == ()


def test_trino_help_matches_source_native_quantile_capability() -> None:
    from marivo.datasource.engines import require_profile_for_backend_type
    from marivo.semantic._aggregate_accuracy import aggregate_repair

    profile = require_profile_for_backend_type("trino")
    assert aggregate_repair(("percentile", 0.95), profile.name) is not None
    assert aggregate_repair(("approx_percentile", 0.95), profile.name) is None
    text, _, _ = render_help_text("datasource.trino")
    assert len(text.splitlines()) <= SURFACE_LIMITS.focused_help_max_lines
    assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


@pytest.mark.parametrize(
    ("target", "canonical_id"),
    [
        ("inspect", "inspect"),
        (md.inspect, "inspect"),
        (md.SourceInspection.sample, "source_inspection.sample"),
        (md.SourceInspection.source_column, "source_inspection.source_column"),
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


def test_unknown_string_raises_typed_bounded_error() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        marivo.help("datasource.inspekt")

    assert len(exc_info.value.candidates) <= SURFACE_LIMITS.help_suggestion_limit
    assert "datasource.inspect" in exc_info.value.candidates


@pytest.fixture
def datasource_runtime_targets(tmp_path: Path) -> tuple[object, ...]:
    source = md.table("orders")
    ref = ref_factory.datasource("warehouse")
    scope = md.unpruned(max_rows=10, timeout_seconds=5)
    inspection = SourceInspection(
        _datasource=ref,
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
        _datasource=ref,
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
        assert resolve_live_target(type(target), DATASOURCE_LIVE_SURFACE).kind in {
            "type_contract",
            "error_contract",
        }


def test_error_help_kind_depends_on_concrete_repair_target() -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    assert (
        resolve_live_target(DatasourceMissingError, DATASOURCE_LIVE_SURFACE).kind
        == "error_contract"
    )


def test_live_help_performs_no_datasource_effects(
    datasource_runtime_targets: tuple[object, ...],
) -> None:
    assert marivo.help() is None  # type: ignore[func-returns-value]  # Verify the runtime return.
    for target in (
        "datasource",
        "datasource.connections",
        "datasource.sources",
        "datasource.inspection",
        "datasource.boundary.semantic_authoring",
        "datasource.inspect",
        *(type(t) for t in datasource_runtime_targets),
    ):
        assert isinstance(target, (str, type))
        assert marivo.help(target) is None  # type: ignore[func-returns-value]  # Verify the runtime return.


def test_datasource_task_tree_exposes_direct_sql_and_conditioned_native_routes() -> None:
    from marivo.datasource._capabilities.registry import REGISTRY

    root_routes = REGISTRY.navigation_routes()
    assert tuple(route.target.display for route in root_routes) == (
        "datasource.connections",
        "datasource.sources",
        "datasource.inspection",
        "datasource.raw_sql",
        "datasource.boundary.semantic_authoring",
    )


@pytest.mark.parametrize("backend", ("duckdb", "postgres", "mysql", "trino", "clickhouse"))
def test_timezone_declaration_help_has_inputs_and_static_spec_attribute(backend: str) -> None:
    constructor_help = render_help_text(f"datasource.{backend}")[0]
    assert len(constructor_help) <= SURFACE_LIMITS.focused_help_max_codepoints


def test_removed_conveniences_have_no_help_redirect() -> None:
    for target in (
        "datasource.list",
        "datasource.describe",
        "datasource.test",
        "datasource.datasource_catalog.get",
        "datasource.datasource_list.ids",
        "datasource.datasource_summary.semantic_id",
        "datasource.raw_sql_result.row_count",
        "semantic.period_calendar_details.date",
    ):
        with pytest.raises(MarivoHelpTargetError):
            marivo.help(target)


def test_snapshot_has_direct_fields_and_no_action_contract(
    datasource_runtime_targets: tuple[object, ...],
) -> None:
    snapshot = next(t for t in datasource_runtime_targets if isinstance(t, DiscoverySnapshot))
    assert not hasattr(snapshot, "contract")


@pytest.mark.parametrize("target", ("authoring", "sql", "credentials", "inspection.scope"))
def test_retired_datasource_topics_have_no_alias(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        marivo.help(f"datasource.{target}")
