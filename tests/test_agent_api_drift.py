"""Drift tests enforcing the agent-friendly public API result contract.

These tests verify:
- Result-producing public APIs do not write stdout.
- Help APIs print bounded help and return None.
- repr() is one line and points to .show().
- render() + show() are present and well-behaved.
- available: sections are present and non-empty.
- display= parameter is absent.
- format= is absent from help APIs.
"""

from __future__ import annotations

import inspect
import textwrap
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.datasource.authoring import DuckDBSpec
from marivo.introspection.live.model import SURFACE_LIMITS
from tests.lazy_observation_fixtures import make_sources

# ---------------------------------------------------------------------------
# Minimal project files for tests that need a loaded SemanticProject
# ---------------------------------------------------------------------------

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_OBJECTS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.dimension(entity=orders)
    def region(table):
        return table.region

    @ms.time_dimension(entity=orders, granularity="day", parse=ms.timestamp(timezone="UTC"))
    def created_at(table):
        return table.created_at

    @ms.metric(entities=[orders], additivity='additive', )
    def total_revenue(table):
        return table.amount.sum()
""")


def _make_project(semantic_project_factory):
    """Create a minimal loaded project for drift tests."""
    return semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _OBJECTS_PY,
        }
    )


def _make_catalog(semantic_project_factory):
    """Create a minimal loaded catalog for drift tests."""
    from marivo.semantic.catalog import SemanticCatalog

    return SemanticCatalog(_make_project(semantic_project_factory))


# ---------------------------------------------------------------------------
# No-stdout contract on public APIs
# ---------------------------------------------------------------------------


def test_catalog_metrics_is_silent(semantic_project_factory, capsys) -> None:
    catalog = _make_catalog(semantic_project_factory)
    _ = catalog.metrics
    assert capsys.readouterr().out == ""


def test_catalog_datasources_is_silent(semantic_project_factory, capsys) -> None:
    catalog = _make_catalog(semantic_project_factory)
    _ = catalog.datasources
    assert capsys.readouterr().out == ""


def test_readiness_is_silent(semantic_project_factory, capsys) -> None:
    project = _make_project(semantic_project_factory)
    project.readiness()
    assert capsys.readouterr().out == ""


def test_richness_is_silent(semantic_project_factory, capsys) -> None:
    project = _make_project(semantic_project_factory)
    project.richness()
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Help APIs return None
# ---------------------------------------------------------------------------


def test_marivo_help_returns_none() -> None:
    assert marivo.help() is None


def test_marivo_help_with_target_returns_none() -> None:
    assert marivo.help("analysis.observe") is None


# ---------------------------------------------------------------------------
# Help APIs reject format=
# ---------------------------------------------------------------------------


def test_marivo_help_has_no_format_or_print_parameter() -> None:
    sig = inspect.signature(marivo.help)
    assert "format" not in sig.parameters
    assert "print" not in sig.parameters


def test_marivo_help_rejects_removed_options() -> None:
    with pytest.raises(TypeError):
        marivo.help("analysis.observe", format="json")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        marivo.help("analysis.observe", print=False)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# repr() is one line and hints .show()
# ---------------------------------------------------------------------------


def test_logical_metric_repr_is_bounded_and_has_identity() -> None:
    result = make_sources().observe(ms.ref.metric("sales.revenue"))
    rendered = repr(result)
    assert "\n" not in rendered
    assert len(rendered) <= 200
    assert "logical" in rendered.lower()
    assert "execute()" in rendered


def test_catalog_collection_repr_is_one_line(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    r = repr(result)
    assert r.count("\n") == 0


# ---------------------------------------------------------------------------
# render() + show() contract
# ---------------------------------------------------------------------------


def test_dataset_contract_render_is_silent(capsys) -> None:
    result = make_sources().observe(ms.ref.metric("sales.revenue")).contract()
    result.render()
    assert capsys.readouterr().out == ""


def test_dataset_contract_show_prints_render_plus_newline(capsys) -> None:
    result = make_sources().observe(ms.ref.metric("sales.revenue")).contract()
    assert result.show() is None
    assert capsys.readouterr().out == result.render() + "\n"


def test_catalog_collection_render_contains_refs_affordance(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    rendered = result.render()
    assert "available:" in rendered
    assert "- .refs" in rendered
    assert "selection: catalog.metrics.get(<displayed ref>) -> MetricEntry" in rendered


def test_datasource_catalog_render_uses_card_listing_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    md.register(
        DuckDBSpec(name="warehouse", path=str(tmp_path / "warehouse.duckdb")),
        project_root=tmp_path,
    )
    catalog = md.load(workspace_dir=tmp_path)

    rendered = catalog.render()

    assert "DatasourceCatalog datasources=1" in rendered
    assert "warehouse:" in rendered
    assert "- backend_type=duckdb" in rendered
    assert "- fields=path:" in rendered
    assert "- env_refs=(none)" in rendered
    assert "- name:" not in rendered
    assert "backend_type: duckdb" not in rendered
    assert repr(catalog).count("\n") == 0

    assert catalog.show() is None
    assert capsys.readouterr().out == rendered + "\n"


def test_catalog_collection_available_never_none(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    # "available: none" should never appear — the available: section lists
    # method entries, never the word "none"
    assert "available: none" not in result.render().lower()


def test_readiness_render_contains_available(semantic_project_factory) -> None:
    project = _make_project(semantic_project_factory)
    report = project.readiness()
    assert "available:" in report.render()


# ---------------------------------------------------------------------------
# Help output stays within line budget
# ---------------------------------------------------------------------------


def test_analysis_help_teaches_state_specific_exits() -> None:
    logical = render_help_text("analysis.datasets.logical")[0]
    materialized = render_help_text("analysis.datasets.materialized")[0]
    assert "execute" in logical
    assert "contract" in logical
    assert "to_pandas" in materialized
    assert "show" in materialized


def test_marivo_help_top_level_within_budget(capsys) -> None:
    marivo.help()
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= SURFACE_LIMITS.root_help_max_lines


def test_marivo_help_topic_within_budget(capsys) -> None:
    marivo.help("analysis.observe")
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= SURFACE_LIMITS.focused_help_max_lines


def test_runtime_ratio_help_teaches_zero_division_policy() -> None:
    rendered = render_help_text("analysis.runtime_metric.ratio")[0]
    assert "zero_division" in rendered
    assert "null" in rendered and "error" in rendered


def test_semantic_help_topic_within_budget(capsys) -> None:
    marivo.help("semantic.metric")
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= 100


# ---------------------------------------------------------------------------
# Default public export surface is pruned to workflow objects
# ---------------------------------------------------------------------------


def test_analysis_public_exports_are_ordered_default_workflow_surface() -> None:
    expected = [
        "Dataset",
        "LogicalDataset",
        "MaterializedDataset",
        "DatasetShapeId",
        "DatasetFieldId",
        "DatasetFieldIdentity",
        "DatasetPhysicalTypeState",
        "DatasetField",
        "DatasetRowBound",
        "DatasetCardinality",
        "DatasetOrderTerm",
        "DatasetOrdering",
        "DatasetByteCount",
        "DatasetFamilyRowSemantics",
        "DatasetRowContract",
        "DatasetRowSetContract",
        "DatasetSchema",
        "LogicalDatasetState",
        "MaterializedDatasetState",
        "DatasetContract",
        "DatasetFields",
        "DatasetFieldRef",
        "LogicalPopulationDataset",
        "MaterializedPopulationDataset",
        "LogicalMetricDataset",
        "MaterializedMetricDataset",
        "LogicalDeltaDataset",
        "MaterializedDeltaDataset",
        "LogicalAttributionDataset",
        "MaterializedAttributionDataset",
        "LogicalAssociationDataset",
        "MaterializedAssociationDataset",
        "LogicalForecastDataset",
        "MaterializedForecastDataset",
        "LogicalCandidateDataset",
        "MaterializedCandidateDataset",
        "LogicalEventDataset",
        "MaterializedEventDataset",
        "LogicalLifecycleDataset",
        "MaterializedLifecycleDataset",
        "AnalysisPredicate",
        "ForecastHorizon",
        "ForecastModel",
        "WindowBucketAlignment",
        "BoundedCompletenessDeclarationV1",
        "SourceOriginCompletenessDeclarationV1",
        "DroppedBefore",
        "EventPattern",
        "EveryStart",
        "FirstPerSubject",
        "FromInception",
        "FunnelLossRate",
        "Grain",
        "InState",
        "PatternStep",
        "TimeScope",
        "ArtifactDigest",
        "ArtifactRef",
        "ArtifactRevalidation",
        "ArtifactSummary",
        "EvidenceIntegrityError",
        "FailedRun",
        "Finding",
        "FindingPage",
        "IncompleteRun",
        "RunPage",
        "SessionGraph",
        "SucceededRun",
        "Session",
        "eq",
        "not_eq",
        "lt",
        "lte",
        "gt",
        "gte",
        "is_in",
        "is_null",
        "is_not_null",
        "all_of",
        "any_of",
        "not_",
        "grain",
        "time_scope",
        "window_bucket",
        "step",
        "sequence",
        "first_per_subject",
        "every_start",
        "dropped_before",
        "in_state",
        "funnel_loss_rate",
        "from_inception",
        "periods",
        "naive",
        "drift",
        "seasonal_naive",
        "runtime_metric",
        "session",
    ]
    assert mv.__all__ == expected
    assert set(dir(mv)) == set(expected)


def test_analysis_dir_hides_advanced_and_internal_objects() -> None:
    hidden = {
        "BaseFrame",
        "BaseFrameMeta",
        "JobSummary",
        "Lineage",
        "LineageStep",
        "BlockingIssue",
        "ConfidenceScope",
        "ComponentFrame",
        "CoverageFrame",
        "errors",
        "evidence",
        "frames",
    }
    assert hidden.isdisjoint(dir(mv))


# ---------------------------------------------------------------------------
# Analysis runtime must not query public catalog collections or direct registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "marivo/analysis/observation/metric.py",
        "marivo/analysis/observation/population.py",
        "marivo/analysis/compiler/normalize.py",
        "marivo/analysis/compiler/lowering.py",
        "marivo/analysis/materialization/admission.py",
    ],
)
def test_analysis_runtime_does_not_query_public_catalog_collections(path: str) -> None:
    source = (Path(__file__).parents[1] / path).read_text()
    assert "catalog.list(" not in source
    assert "catalog._reg" not in source
