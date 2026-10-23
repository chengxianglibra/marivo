"""Datasource live-help target resolution and runtime behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import marivo
import marivo.datasource as md
from marivo._authoring.model import AuthoringRepair
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
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.refs import ref as ref_factory


def test_trino_help_matches_source_native_quantile_capability() -> None:
    from marivo.datasource.engines import require_profile_for_backend_type
    from marivo.semantic._aggregate_accuracy import aggregate_repair

    profile = require_profile_for_backend_type("trino")
    assert not profile.exact_quantile
    assert aggregate_repair(("percentile", 0.95), profile.name) is not None
    assert aggregate_repair(("approx_percentile", 0.95), profile.name) is None
    text, _, _ = render_help_text("datasource.trino")
    callable_text, _, _ = render_help_text(md.trino)
    assert text == callable_text
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


@pytest.mark.parametrize(
    "target",
    ("source_inspection.sample",),
)
def test_registered_sample_string_paths_resolve_to_one_descriptor(target: str) -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    resolved = resolve_live_target(target, DATASOURCE_LIVE_SURFACE)
    assert resolved.kind == "descriptor"
    assert resolved.canonical_id == "source_inspection.sample"


def test_inspection_source_column_help_routes_to_exact_method() -> None:
    from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    for target in (
        "source_inspection.source_column",
        md.SourceInspection.source_column,
    ):
        resolved = resolve_live_target(target, DATASOURCE_LIVE_SURFACE)
        assert resolved.canonical_id == "source_inspection.source_column"


def test_unknown_string_raises_typed_bounded_error() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        marivo.help("datasource.inspekt")

    assert len(exc_info.value.candidates) <= SURFACE_LIMITS.help_suggestion_limit
    assert "datasource.inspect" in exc_info.value.candidates


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


@pytest.mark.parametrize("name", ("list", "describe", "test"))
def test_catalog_operations_keep_canonical_callable_help(name: str) -> None:
    text = render_help_text(f"datasource.datasource_catalog.{name}")[0]
    assert text == render_help_text(getattr(DatasourceCatalog, name))[0]
    assert f"catalog.{name}" in text


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
    from marivo.datasource.errors import DatasourceHelpTargetError
    from marivo.introspection.live.resolve import resolve_live_target

    for target in datasource_runtime_targets:
        with pytest.raises(DatasourceHelpTargetError):
            resolve_live_target(target, DATASOURCE_LIVE_SURFACE)
        assert resolve_live_target(type(target), DATASOURCE_LIVE_SURFACE).kind in {
            "type_contract",
            "error_contract",
        }


def test_advertised_datasource_members_resolve_through_public_help() -> None:
    from marivo.datasource._capabilities.registry import TYPE_CONTRACTS
    from marivo.introspection.live.targets import canonical_target

    rendered_methods: dict[object, str] = {}
    for type_obj, contract in TYPE_CONTRACTS.items():
        for name in contract.public_methods:
            method = getattr(type_obj, name)
            # Shared retained-data display methods have no datasource-specific owner.
            if method.__module__ == "marivo._data_render":
                continue
            text = rendered_methods.get(method)
            if text is None:
                text, _, _ = render_help_text(method)
                rendered_methods[method] = text
            assert text
            if method.__module__.startswith("marivo.datasource"):
                assert (
                    text
                    == render_help_text(f"datasource.{canonical_target(contract.name)}.{name}")[0]
                )


def test_snapshot_has_direct_fields_and_no_action_contract(
    datasource_runtime_targets: tuple[object, ...],
) -> None:
    snapshot = next(t for t in datasource_runtime_targets if isinstance(t, DiscoverySnapshot))
    assert not hasattr(snapshot, "contract")
    text = render_help_text("datasource.discovery_snapshot")[0]
    assert "retained_values" in text
    assert "coverage" in text


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

    from marivo.datasource.errors import DatasourceHelpTargetError

    for error in (with_repair, without_repair):
        with pytest.raises(DatasourceHelpTargetError):
            resolve_live_target(error, DATASOURCE_LIVE_SURFACE)
    assert (
        resolve_live_target(DatasourceMissingError, DATASOURCE_LIVE_SURFACE).kind
        == "error_contract"
    )


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
    for target in (
        "datasource",
        "datasource.connections",
        "datasource.sources",
        "datasource.inspection",
        "datasource.boundary.semantic_authoring",
        "datasource.inspect",
        md.SourceInspection,
        *(type(t) for t in datasource_runtime_targets),
    ):
        assert marivo.help(target) is None


def test_datasource_task_tree_exposes_direct_sql_and_conditioned_native_routes() -> None:
    from marivo._help.render import render_help_text
    from marivo.datasource._capabilities.registry import REGISTRY

    root_routes = REGISTRY.navigation_routes()
    assert tuple(route.target.display for route in root_routes) == (
        "datasource.connections",
        "datasource.sources",
        "datasource.inspection",
        "datasource.raw_sql",
        "datasource.boundary.semantic_authoring",
    )
    root = render_help_text("datasource")[0]
    assert "default" not in root
    assert "median" not in root
    for topic in ("", "connections", "sources", "inspection"):
        page = render_help_text("datasource" + (f".{topic}" if topic else ""))[0]
        for route in REGISTRY.navigation_routes(topic):
            assert route.summary in page
            assert f'marivo.help("{route.target.display}")' in page
            assert render_help_text(route.target.display)[0]
    connections = render_help_text("datasource.connections")[0]
    assert "host resolver" in connections
    assert "datasource.credential_scope" in connections
    assert "datasource.secret_value.reveal" not in connections
    inspection = render_help_text("datasource.inspection")[0]
    assert "samples do not determine business meaning" in inspection
    assert "datasource.raw_sql" in inspection
    assert 'marivo.help("semantic")' not in inspection
    handoff = render_help_text("datasource.boundary.semantic_authoring")[0]
    assert 'marivo.help("semantic")' in handoff
    assert "business authority supplies reusable meaning" in handoff


@pytest.mark.parametrize("target", ("authoring", "sql", "credentials", "inspection.scope"))
def test_retired_datasource_topics_have_no_alias(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        marivo.help(f"datasource.{target}")
