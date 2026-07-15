"""Datasource live-help target and render contracts."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import get_type_hints

import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import DatasourceSpec
from marivo.datasource.catalog import DatasourceCatalog
from marivo.datasource.errors import (
    DatasourceError,
    DatasourceHelpTargetError,
    DatasourceMissingError,
    repair,
)
from marivo.datasource.evidence import (
    DimensionEvidenceResult,
    DimensionValuesResult,
    EntityEvidenceResult,
    MeasureEvidenceResult,
    RelationshipEvidenceResult,
    TimeEvidenceResult,
)
from marivo.datasource.inspection import (
    ExecutionCapabilities,
    Partitioning,
    PhysicalExtent,
    SourceInspection,
)
from marivo.datasource.snapshot import DiscoverySnapshot, SnapshotCoverage
from marivo.datasource.source import TableSource
from marivo.introspection.live.model import SURFACE_LIMITS


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
    text = md.help_text(target)  # type: ignore[arg-type]
    assert canonical_id in text


def test_unknown_string_raises_typed_bounded_error() -> None:
    with pytest.raises(DatasourceHelpTargetError) as exc_info:
        md.help_text("inspekt")

    assert exc_info.value.repair is not None
    assert len(exc_info.value.repair.candidates) <= SURFACE_LIMITS.help_suggestion_limit
    assert "inspect" in exc_info.value.repair.candidates


def test_root_help_reveals_current_environment() -> None:
    text = md.help_text()

    assert f"Marivo: {marivo.__version__}" in text
    assert f"Python: {Path(sys.executable).resolve()}" in text
    assert f"Package: {Path(marivo.__file__).resolve()}" in text


def test_public_help_annotations_keep_the_exact_concrete_target_union() -> None:
    from marivo.datasource.help import PublicDatasourceHelpTarget

    evidence_result = (
        EntityEvidenceResult
        | DimensionEvidenceResult
        | DimensionValuesResult
        | TimeEvidenceResult
        | MeasureEvidenceResult
        | RelationshipEvidenceResult
    )
    expected = (
        str
        | Callable[..., object]
        | type[object]
        | md.DatasourceRef
        | DatasourceSpec
        | md.DatasourceCatalog
        | md.DatasourceSummary
        | md.DatasourceDescription
        | md.DatasourceTestResult
        | md.DatasourceConnection
        | TableSource
        | md.PartitionScope
        | md.UnprunedScope
        | md.SourceInspection
        | md.DiscoverySnapshot
        | evidence_result
        | DatasourceError
        | None
    )

    assert PublicDatasourceHelpTarget == expected
    assert get_type_hints(md.help_text)["target"] == expected
    assert get_type_hints(md.help)["target"] == expected


@pytest.fixture
def datasource_runtime_targets(tmp_path: Path) -> tuple[object, ...]:
    source = md.table("orders")
    ref = md.ref("datasource.warehouse")
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
        ref,
        DatasourceCatalog(workspace_dir=tmp_path),
        source,
        scope,
        inspection,
        snapshot,
        EntityEvidenceResult(
            status="complete",
            snapshot_id="snapshot-1",
            columns=(),
            evidence_by_column={},
            issues=(),
            repair=None,
        ),
        DatasourceMissingError(message="warehouse is missing"),
    )


def test_runtime_help_accepts_only_registered_datasource_instances(
    datasource_runtime_targets: tuple[object, ...],
) -> None:
    for target in datasource_runtime_targets:
        text = md.help_text(target)  # type: ignore[arg-type]
        assert text.strip()


def test_projection_result_help_points_to_contract_and_repair_without_values() -> None:
    result = DimensionValuesResult(
        status="incomplete",
        snapshot_id="snapshot-1",
        column="region",
        sample_distinct_count=1,
        returned_value_count=1,
        sample_values_complete=False,
        scope_values_complete=False,
        value_evidence_state="available",
        frequency_capacity=10,
        values=(("private-region-value", 1),),
        issues=("requested_limit_bounded",),
        repair=repair(
            kind="reacquire",
            canonical_id="SourceInspection.sample",
            action="Reacquire bounded retained values.",
            preserves_evidence=False,
        ),
    )

    text = md.help_text(result)

    assert "Continuation: call .contract()" in text
    assert "repair" in text
    assert "private-region-value" not in text


def test_help_rejects_cross_surface_private_and_ambiguous_targets() -> None:
    from marivo.analysis import MetricFrame
    from marivo.datasource.authoring import _SpecBase

    for target in (ms.ref("metric.sales.revenue"), MetricFrame, _SpecBase, object(), "source"):
        with pytest.raises(DatasourceHelpTargetError) as exc_info:
            md.help_text(target)  # type: ignore[arg-type]
        if target is MetricFrame:
            assert "mv.help" in str(exc_info.value)


@pytest.mark.parametrize(
    ("target", "adapter"),
    [
        (ms.ref, "ms.help"),
        (mv.Session, "mv.help"),
    ],
)
def test_cross_surface_callable_rejection_names_owning_adapter(
    target: object, adapter: str
) -> None:
    with pytest.raises(DatasourceHelpTargetError) as exc_info:
        md.help_text(target)  # type: ignore[arg-type]
    assert adapter in str(exc_info.value)


def test_live_help_performs_no_datasource_effects(
    datasource_runtime_targets: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform datasource effects")

    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend_with_secrets", fail)
    monkeypatch.setattr("marivo.datasource.authoring_store.AuthoringStore.write_snapshot", fail)
    monkeypatch.setattr("marivo.config.load_project_config", fail)

    assert md.help_text()
    for target in ("inspect", md.SourceInspection, *datasource_runtime_targets):
        assert md.help_text(target)  # type: ignore[arg-type]
