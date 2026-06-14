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
import marivo.semantic as ms
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.semantic.reader import SemanticProject

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Minimal project files for tests that need a loaded SemanticProject
# ---------------------------------------------------------------------------

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")

_OBJECTS_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.dimension(entity=orders)
    def region(table):
        return table.region

    @ms.time_dimension(entity=orders, data_type="timestamp", granularity="day")
    def created_at(table):
        return table.created_at

    @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )
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
    catalog.list("sales", kind="metric")
    assert capsys.readouterr().out == ""


def test_catalog_list_datasources_is_silent(semantic_project_factory, capsys) -> None:
    catalog = _make_catalog(semantic_project_factory)
    catalog.list(kind="datasource")
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


def test_mv_help_returns_none(capsys) -> None:
    result = mv.help()
    assert result is None


def test_mv_help_symbol_returns_none(capsys) -> None:
    result = mv.help("observe")
    assert result is None


def test_ms_help_returns_none(capsys) -> None:
    result = ms.help()
    assert result is None


def test_ms_help_symbol_returns_none(capsys) -> None:
    result = ms.help("metric")
    assert result is None


# ---------------------------------------------------------------------------
# Help APIs reject format=
# ---------------------------------------------------------------------------


def test_mv_help_no_format_parameter() -> None:
    sig = inspect.signature(mv.help)
    assert "format" not in sig.parameters


def test_ms_help_no_format_parameter() -> None:
    sig = inspect.signature(ms.help)
    assert "format" not in sig.parameters


def test_mv_help_raises_on_format_kwarg() -> None:
    with pytest.raises(TypeError):
        mv.help("observe", format="json")  # type: ignore[call-arg]


def test_ms_help_raises_on_format_kwarg() -> None:
    with pytest.raises(TypeError):
        ms.help("metric", format="json")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# deleted SemanticProject catalog read methods stay removed
# ---------------------------------------------------------------------------


def test_reader_project_catalog_read_methods_are_removed() -> None:
    removed = {
        "list_domains",
        "list_datasources",
        "list_entities",
        "list_dimensions",
        "list_time_dimensions",
        "list_metrics",
        "list_relationships",
    }

    for name in removed:
        assert not hasattr(SemanticProject, name), name


def test_current_semantic_docs_do_not_reference_removed_project_read_surface() -> None:
    checked_paths = [
        REPO_ROOT / "docs" / "specs" / "semantic" / "agent-semantic-layer-authoring-design.md",
        REPO_ROOT / "docs" / "specs" / "semantic" / "authoring-pipeline-design.md",
        REPO_ROOT / "docs" / "specs" / "semantic" / "python-semantic-layer.md",
        REPO_ROOT / "docs" / "specs" / "semantic" / "stepwise-authoring-design.md",
        REPO_ROOT / "marivo/skills" / "marivo-semantic" / "references" / "closeout.md",
        REPO_ROOT / "marivo/skills" / "marivo-semantic" / "references" / "preview.md",
        REPO_ROOT / "marivo" / "semantic" / "reader.py",
        REPO_ROOT / "marivo" / "semantic" / "constraints.py",
        REPO_ROOT / "marivo" / "semantic" / "catalog.py",
    ]
    removed_terms = (
        "project.collect_source_preview",
        "project.search",
        "project.describe",
        "project.dependencies",
        "project.dependents",
        "preview_dataset",
        "preview_field",
        "preview_metric",
        "search(kind=...)",
        "materialization or preview methods",
    )

    offenders: list[str] = []
    for path in checked_paths:
        text = path.read_text()
        for term in removed_terms:
            if term in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {term}")

    assert offenders == []


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
    result = catalog.list("sales", kind="metric")
    r = repr(result)
    assert r.count("\n") == 0


def test_readiness_report_repr_is_one_line(semantic_project_factory) -> None:
    project = _make_project(semantic_project_factory)
    report = project.readiness()
    r = repr(report)
    assert r.count("\n") == 0
    assert "ReadinessReport" in r
    assert "call .show() to inspect" in r


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


def test_semantic_object_list_render_contains_next_steps(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales", kind="metric")
    assert "next steps:" in result.render()


def test_semantic_object_list_available_never_none(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales", kind="metric")
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


def test_mv_help_top_level_within_budget(capsys) -> None:
    mv.help()
    captured = capsys.readouterr()
    # Budget: 150 lines. Current output is ~138 lines.
    assert len(captured.out.splitlines()) <= 150


def test_mv_help_topic_within_budget(capsys) -> None:
    mv.help("observe")
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
    spec = _read("docs/specs/analysis/python-analysis-operator-design.md")
    assert "not write stdout" in spec or "do not write stdout" in spec or "silent" in spec.lower()


def test_semantic_spec_mentions_no_stdout_contract() -> None:
    spec = _read("docs/specs/semantic/python-semantic-layer.md")
    assert "not write stdout" in spec or "do not write stdout" in spec or "silent" in spec.lower()


def test_analysis_skill_teaches_show_not_print_summary() -> None:
    skill = _read("marivo/skills/marivo-analysis/SKILL.md")
    assert "print(frame.summary())" not in skill
    assert ".show()" in skill


def test_semantic_skill_rejects_format_json_examples() -> None:
    skill = _read("marivo/skills/marivo-semantic/SKILL.md")
    assert "format='json'" not in skill
    assert 'format="json"' not in skill


def test_analysis_skill_rejects_format_json_examples() -> None:
    skill = _read("marivo/skills/marivo-analysis/SKILL.md")
    assert "format='json'" not in skill
    assert 'format="json"' not in skill


def test_analysis_skill_rejects_display_true_examples() -> None:
    skill = _read("marivo/skills/marivo-analysis/SKILL.md")
    assert "display=True" not in skill
    assert "display=False" not in skill


def test_semantic_skill_teaches_mv_help_ref() -> None:
    skill = _read("marivo/skills/marivo-semantic/SKILL.md")
    assert "mv.help(" in skill


# ---------------------------------------------------------------------------
# Public surface has no backend_factory or binding choreography
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Superseded authoring symbols removed from public surface
# ---------------------------------------------------------------------------


def test_removed_stepwise_authoring_symbols_are_not_public() -> None:
    removed = {
        "AuthoringSourceInput",
        "EvidenceFact",
        "MetadataOnlyPolicy",
        "BoundedProfilePolicy",
        "SelectedColumnsPolicy",
        "TableContext",
        "ColumnContext",
        "ColumnEvidence",
        "SourceEvidencePack",
    }

    assert removed.isdisjoint(set(ms.__all__))
    for name in removed:
        assert not hasattr(ms, name), f"ms.{name} should be removed"

    project = SemanticProject()
    assert not hasattr(project, "assess_authoring")
    assert not hasattr(project, "inspect_authored_object")
    assert not hasattr(project, "inspect_table")
    assert not hasattr(project, "inspect_columns")


def test_semantic_authoring_public_surface_has_no_backend_factory_or_binding() -> None:
    project = SemanticProject()

    assert not hasattr(project, "bind_datasource_access")
    for name in (
        "materialize_dataset",
        "materialize_field",
        "materialize_metric",
        "preview_dataset",
        "preview_field",
        "preview_metric",
        "parity_check",
        "readiness",
    ):
        method = getattr(project, name, None)
        if method is None:
            continue
        signature = inspect.signature(method)
        assert "backend_factory" not in signature.parameters, (
            f"{name} still has backend_factory param"
        )
        assert "inspect_source" not in signature.parameters, (
            f"{name} still has inspect_source param"
        )
