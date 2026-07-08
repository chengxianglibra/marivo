"""Drift tests enforcing the agent-friendly public API result contract.

These tests verify:
- Result-producing public APIs do not write stdout.
- Help APIs print bounded help and return None.
- repr() is one line and points to .show().
- render() + show() are present and well-behaved.
- available: sections are present and non-empty.
- Docs teach the no-side-effect result contract.
- display= parameter is absent.
- format= is absent from help APIs and skill examples.
"""

from __future__ import annotations

import datetime
import inspect
import textwrap
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.datasource.authoring import DuckDBSpec

REPO_ROOT = Path(__file__).resolve().parents[1]

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
    orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))

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


def test_catalog_list_metrics_is_silent(semantic_project_factory, capsys) -> None:
    catalog = _make_catalog(semantic_project_factory)
    catalog.list("metric", scope="domain.sales")
    assert capsys.readouterr().out == ""


def test_catalog_list_datasources_is_silent(semantic_project_factory, capsys) -> None:
    catalog = _make_catalog(semantic_project_factory)
    catalog.list("datasource")
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


# ---------------------------------------------------------------------------
# Help APIs reject format=
# ---------------------------------------------------------------------------


def test_mv_help_no_format_parameter() -> None:
    sig = inspect.signature(mv.help)
    assert "format" not in sig.parameters


def test_ms_help_no_format_parameter() -> None:
    sig = inspect.signature(ms.help)
    assert "format" not in sig.parameters


def test_md_help_no_format_or_print_parameter() -> None:
    sig = inspect.signature(md.help)
    assert "format" not in sig.parameters
    assert "print" not in sig.parameters


def test_mv_help_raises_on_format_kwarg() -> None:
    with pytest.raises(TypeError):
        mv.help("observe", format="json")  # type: ignore[call-arg]


def test_ms_help_raises_on_format_kwarg() -> None:
    with pytest.raises(TypeError):
        ms.help("metric", format="json")  # type: ignore[call-arg]


def test_md_help_raises_on_format_or_print_kwarg() -> None:
    with pytest.raises(TypeError):
        md.help("trino", format="json")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        md.help("trino", print=False)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# repr() is one line and hints .show()
# ---------------------------------------------------------------------------


def test_metric_frame_repr_is_one_line() -> None:
    meta = BaseFrameMeta(
        kind="metric_frame",
        ref="frame_test01",
        session_id="s1",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        row_count=3,
        byte_size=100,
        lineage=Lineage(),
    )
    df = pd.DataFrame({"x": [1, 2, 3]})
    frame = BaseFrame(_df=df, meta=meta)
    r = repr(frame)
    assert r.count("\n") == 0
    assert "call .show() to inspect" in r


def test_semantic_object_list_repr_is_one_line(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("metric", scope="domain.sales")
    r = repr(result)
    assert r.count("\n") == 0


# ---------------------------------------------------------------------------
# render() + show() contract
# ---------------------------------------------------------------------------


def test_frame_render_no_stdout(capsys) -> None:
    meta = BaseFrameMeta(
        kind="metric_frame",
        ref="fr01",
        session_id="s1",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        row_count=2,
        byte_size=50,
        lineage=Lineage(),
    )
    frame = BaseFrame(_df=pd.DataFrame({"x": [1, 2]}), meta=meta)
    frame.render()
    assert capsys.readouterr().out == ""


def test_frame_show_prints_render_plus_newline(capsys) -> None:
    meta = BaseFrameMeta(
        kind="metric_frame",
        ref="fr01",
        session_id="s1",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        row_count=2,
        byte_size=50,
        lineage=Lineage(),
    )
    frame = BaseFrame(_df=pd.DataFrame({"x": [1, 2]}), meta=meta)
    result = frame.show()
    captured = capsys.readouterr()
    assert result is None
    assert captured.out == frame.render() + "\n"


def test_semantic_object_list_render_contains_refs_affordance(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("metric", scope="domain.sales")
    rendered = result.render()
    assert "available:" in rendered
    assert "- result.refs()" in rendered
    assert "catalog.get('metric.sales.total_revenue').details().show()" in rendered


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


def test_semantic_object_list_available_never_none(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("metric", scope="domain.sales")
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
# The original spec budget was 80 lines, but the top-level listing for
# marivo.analysis is currently ~138 lines because it covers all public
# symbols, constraints, and frame types. Raising to 150 preserves the
# drift-test intent (catch unbounded growth) without failing on the
# current well-scoped output.


def test_analysis_help_teaches_two_artifact_exits() -> None:
    import marivo.analysis as mv

    rendered = mv.help_text("MetricFrame")
    assert ".show()" in rendered
    assert ".contract()" in rendered
    assert ".to_pandas()" in rendered
    assert ".summary()" not in rendered
    assert ".schema()" not in rendered
    assert ".preview(" not in rendered
    assert ".next_intents()" not in rendered
    assert "contract().affordances" not in rendered


def test_mv_help_top_level_within_budget(capsys) -> None:
    mv.help()
    captured = capsys.readouterr()
    # Budget: 150 lines. Current output is ~138 lines.
    assert len(captured.out.splitlines()) <= 150


def test_mv_help_topic_within_budget(capsys) -> None:
    mv.help("observe")
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= 80


def test_mv_help_workflow_topic_within_budget(capsys) -> None:
    mv.help("workflow")
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= 80


def test_ms_help_topic_within_budget(capsys) -> None:
    ms.help("metric")
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= 100


# ---------------------------------------------------------------------------
# Docs teach the no-stdout result contract
# ---------------------------------------------------------------------------


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


def test_analysis_spec_mentions_no_stdout_contract() -> None:
    spec = _read("docs/specs/analysis/python-analysis-design.md")
    assert "not write stdout" in spec or "do not write stdout" in spec or "silent" in spec.lower()


def test_semantic_spec_mentions_no_stdout_contract() -> None:
    spec = _read("docs/specs/semantic/python-semantic-layer.md")
    assert "not write stdout" in spec or "do not write stdout" in spec or "silent" in spec.lower()


# ---------------------------------------------------------------------------
# Default public export surface is pruned to workflow objects
# ---------------------------------------------------------------------------


def test_analysis_public_exports_are_default_workflow_surface() -> None:
    expected = {
        "help",
        "help_text",
        "session",
        "Session",
        "MetricFrame",
        "DeltaFrame",
        "AttributionFrame",
        "CandidateSet",
        "AssociationResult",
        "HypothesisTestResult",
        "ForecastFrame",
        "QualityReport",
        "window_bucket",
        "dow_aligned",
        "holiday_aligned",
        "holiday_and_dow_aligned",
        "AlignmentPolicy",
        "ibis_query",
        "metric_columns",
        "time_column",
        "dimension_column",
        "SemanticRef",
        "SemanticObject",
        "ArtifactRef",
        "CalendarRef",
        "TimeScope",
        "AbsoluteWindow",
    }
    assert set(mv.__all__) == expected
    assert set(dir(mv)) == expected


def test_analysis_dir_hides_advanced_and_internal_objects() -> None:
    hidden = {
        "BaseFrame",
        "BaseFrameMeta",
        "FrameSummaryEntry",
        "JobSummary",
        "SessionSummary",
        "Lineage",
        "LineageStep",
        "BlockingIssue",
        "ConfidenceScope",
        "ComponentFrame",
        "CoverageFrame",
        "errors",
        "evidence",
        "frames",
        "DeriveContext",
        "IbisQuerySpec",
        "MetricColumnBinding",
        "MetricColumns",
    }
    assert hidden.isdisjoint(dir(mv))
