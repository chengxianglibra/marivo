"""Execute current bilingual workflow and evidence examples against public APIs."""

import re
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory, DslScenario

ROOT = Path(__file__).resolve().parents[1]


def _blocks(language: str, page: str) -> tuple[str, ...]:
    prefix = "docs" if language == "en" else "zh-cn/docs"
    path = ROOT / f"site/src/content/docs/{prefix}/latest/concepts/{page}.mdx"
    return tuple(
        match[1]
        for match in re.findall(
            r"(?m)^(```|~~~)python\n(.*?)^\1\s*$", path.read_text(), flags=re.DOTALL
        )
    )


@pytest.mark.parametrize(
    "page,count", [("analysis-workflow", 20), ("evidence", 2), ("semantic-layer", 47)]
)
def test_bilingual_examples_have_identical_executable_contracts(page: str, count: int) -> None:
    assert len(_blocks("en", page)) == count
    assert _blocks("en", page) == _blocks("zh", page)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "scenario,part,expected_type",
    [
        ("j2", "change", mv.MaterializedStatisticRelation),
        ("j3", "ratio_routes", mv.MaterializedRolledRatioRelation),
        ("j4", "order_count", mv.MaterializedAssociationResult),
    ],
)
def test_first_round_workflow_examples_execute(
    analysis_dsl_case_factory: DslCaseFactory,
    scenario: DslScenario,
    part: str,
    expected_type: type[object],
) -> None:
    case = analysis_dsl_case_factory(scenario)
    namespace: dict[str, object] = {"session": case.session, "mv": mv, "ms": ms}
    first = next(
        block for block in _blocks("en", "analysis-workflow") if block.startswith("customers =")
    )
    exec(compile(first, "first-round-entry-example", "exec"), namespace)
    assert isinstance(namespace["total"], mv.MaterializedRolledNumericRelation)

    followup = next(
        block for block in _blocks("en", "analysis-workflow") if block.startswith("july =")
    )
    change, remainder = followup.split("ratio_routes =", 1)
    ratio, association = remainder.split("order_count =", 1)
    selected = (
        change
        if part == "change"
        else "ratio_routes =" + ratio
        if part == "ratio_routes"
        else "order_count =" + association
    )
    exec(compile(selected, "first-round-continuation-example", "exec"), namespace)
    output = {
        "change": "next_month_mean",
        "ratio_routes": "overall_aov",
        "order_count": "association",
    }[part]
    assert isinstance(namespace[output], expected_type)


@pytest.mark.runtime
def test_workflow_mean_rollup_merges_retained_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import replace

    from tests.shared_fixtures import DSL_NAMES

    case = analysis_dsl_case_factory("j1", names=replace(DSL_NAMES, order="orders"))
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\nmean_amount = ms.aggregate(name='mean_amount', measure=amount, agg='mean', time=ordered_at)\n"
    )
    ms.load(workspace_dir=case.root)
    namespace: dict[str, object] = {"session": case.session, "ms": ms, "mv": mv}
    code = next(
        block for block in _blocks("en", "analysis-workflow") if block.startswith("mean_amount =")
    )
    exec(compile(code, "mean-rollup-example", "exec"), namespace)
    overall = namespace["overall"]
    assert isinstance(overall, mv.MaterializedRolledNumericRelation)
    assert overall.to_pandas()["value"].tolist() == pytest.approx([1000 / 3])


@pytest.mark.runtime
def test_deferred_semantic_monthly_observation_rejects_before_run(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    model = authoring_evidence_project / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "name='log_date', entity=orders", "name='order_date', entity=orders"
        )
    )
    with duckdb.connect(str(authoring_evidence_project / "warehouse.duckdb")) as connection:
        identifiers = connection.execute("SELECT query_id FROM orders ORDER BY query_id").fetchall()
        assert len(identifiers) == 4
        for identifier, day, amount in zip(
            identifiers,
            ("20260101", "20260201", "20260301", "20260401"),
            (10, 20, 30, 40),
            strict=True,
        ):
            connection.execute(
                "UPDATE orders SET log_date=?, amount=? WHERE query_id=?",
                [day, amount, identifier[0]],
            )
    session = mv.session.get_or_create("semantic-example", report_timezone="UTC")
    namespace: dict[str, object] = {
        "mv": mv,
        "ms": ms,
        "session": session,
        "catalog": session.catalog,
    }
    code = next(
        block
        for block in _blocks("en", "semantic-layer")
        if block.startswith('revenue_entry = catalog.metrics.get("sales.revenue")')
    )
    exec(compile(code, "semantic-observation-example", "exec"), namespace)
    logical = namespace["dataset"]
    assert isinstance(logical, mv.LogicalMetricDataset)
    assert session.runs().items == ()
    assert not session._runtime.statistics.statements
    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError, match="R6–R9"):
        logical.execute()
    assert session.runs().items == ()


@pytest.mark.runtime
def test_workflow_evidence_and_cold_recovery_examples(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    namespace: dict[str, object] = {}
    exec(compile(_blocks("en", "analysis-workflow")[0], "workflow-example", "exec"), namespace)
    change = namespace["change"]
    assert isinstance(change, mv.MaterializedDifferenceRelation)
    expected = {"A": -40, "B": 20, "C": -50, "D": 0}
    assert change.to_pandas().set_index("member")["value"].to_dict() == expected
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    assert len(session.runs().items) == 1
    exec(compile(_blocks("en", "evidence")[0], "evidence-example", "exec"), namespace)
    # Repeated source definitions must create a new evaluation and immutable Artifact.
    assert len(session.runs().items) == 2
    second = namespace["artifact"]
    assert isinstance(second, mv.MaterializedDifferenceRelation)
    assert second.state.artifact_ref != change.state.artifact_ref
    case.database_path.unlink()
    namespace["session"] = mv.session.resume(session.id, by="id")
    namespace["run_id"] = change.state.producing_run_ref
    exec(compile(_blocks("en", "evidence")[1], "recovery-example", "exec"), namespace)
    recovered = namespace["artifact"]
    assert isinstance(recovered, mv.MaterializedDifferenceRelation)
    assert recovered.state.artifact_ref == change.state.artifact_ref
    assert recovered.to_pandas().set_index("member")["value"].to_dict() == expected
    assert len(session.runs().items) == 2


@pytest.mark.runtime
def test_coordinate_row_statistic_workflow_example(analysis_dsl_case_factory: DslCaseFactory):
    case = analysis_dsl_case_factory("j1")
    namespace: dict[str, object] = {"session": case.session, "mv": mv, "ms": ms}
    block = next(b for b in _blocks("en", "analysis-workflow") if b.startswith("all_members ="))
    exec(compile(block, "r54-coordinate-example", "exec"), namespace)
    counts = namespace["category_counts"]
    targets = namespace["fixed_targets"]
    assert isinstance(counts, mv.MaterializedStatisticRelation)
    assert isinstance(targets, mv.MaterializedAnalysisDomain)
    assert counts.to_pandas().set_index("group")["value"].to_dict() == {
        "east": 2,
        "south": 0,
        "west": 0,
    }
    assert targets.to_pandas()["group"].tolist() == ["east", "south", "west"]
    total = namespace["total_row_mean"]
    assert isinstance(total, mv.MaterializedStatisticRelation)
    assert total.to_pandas()["value"].tolist() == [300]


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_display_workflow_example_executes(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    from tests.shared_fixtures import export_dsl_parquet_models

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    namespace = {
        "session": case.session,
        "mv": mv,
        "region": members.read(ms.ref.dimension("sales.customer.region")),
        "counts": members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        ),
    }
    code = next(
        block for block in _blocks("en", "analysis-workflow") if block.startswith("ranking =")
    )
    exec(compile(code, "r65-display-example", "exec"), namespace)
    result = namespace["result"]
    assert isinstance(result, mv.MaterializedTable)
    assert result.to_pandas().columns.tolist() == ["member", "amount", "rank"]
    assert result.to_pandas().equals(namespace["restored"].to_pandas())


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_attribution_workflow_example_executes(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
) -> None:
    from tests.shared_fixtures import export_dsl_parquet_models

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    namespace = {"session": case.session, "mv": mv, "ms": ms}
    code = next(
        block
        for block in _blocks("en", "analysis-workflow")
        if block.startswith("attribution_members =")
    )
    assert code in _blocks("zh-cn", "analysis-workflow")
    exec(compile(code, "r66-attribution-example", "exec"), namespace)
    allocation = namespace["allocation"]
    restored = namespace["restored_allocation"]
    table = namespace["allocated_table"]
    assert isinstance(allocation, mv.MaterializedAttributionResult)
    assert isinstance(restored, mv.MaterializedAttributionResult)
    assert isinstance(table, mv.MaterializedTable)
    assert allocation.contribution.to_pandas().equals(restored.contribution.to_pandas())
    assert table.to_pandas()["contribution"].sum() == 0
