"""Execute current bilingual workflow and evidence examples against public APIs."""

import ast
import re
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory, DslScenario, analysis_dsl_rows
from tests.support.documentation import _example, _examples
from tests.support.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def test_bilingual_examples_have_identical_executable_contracts() -> None:
    english = _examples("en")
    assert english
    assert english == _examples("zh")
    root = ROOT / "site/src/content/docs/docs/latest"
    for path in root.rglob("*.mdx"):
        if "release-notes" in path.parts or path.name == "contributing.mdx":
            continue
        page = path.relative_to(root).with_suffix("").as_posix()
        assert _examples("en", page) == _examples("zh", page), page


def test_latest_usage_examples_are_named_valid_python_and_current() -> None:
    examples = _examples("en")
    for identifier, source in examples.items():
        assert not re.search(r"[\u4e00-\u9fff]", source), identifier
        ast.parse(source, filename=identifier)
    for edition in ("docs", "zh-cn/docs"):
        root = ROOT / "site/src/content/docs" / edition / "latest"
        for page in root.rglob("*.mdx"):
            if "release-notes" in page.parts or page.name == "contributing.mdx":
                continue
            text = page.read_text()
            relative = page.relative_to(root).with_suffix("").as_posix()
            fences = re.findall(r"(?m)^(```|~~~)python\n", text)
            assert len(fences) == len(_examples(edition, relative)), relative
            prose = re.sub(r"(?ms)^(```|~~~).*?^\1\s*$", "", text)
            assert not re.search(r"\b(?:R\d+(?:\.\d+)?|J[1-4]|r8_numeric_v1)\b", prose), relative


@pytest.mark.runtime
def test_grouped_same_entity_workflow_example_executes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    namespace: dict[str, object] = {"session": case.session, "mv": mv, "ms": ms}
    code = _example("en", "same-entity-grouping")
    exec(compile(code, "grouped-same-entity-example", "exec"), namespace)
    result = namespace["channel_revenue"]
    assert isinstance(result, mv.MaterializedNumericRelation)
    rows = result.to_pandas()
    assert dict(zip(rows["group"], rows["value"], strict=True)) == {"web": 850, "mobile": 150}


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
    first = _example("en", "customer-original-rollup")
    exec(compile(first, "first-round-entry-example", "exec"), namespace)
    assert isinstance(namespace["total"], mv.MaterializedNumericRelation)
    total_count = namespace["total_count"]
    merged_count = namespace["merged_count"]
    assert isinstance(total_count, mv.MaterializedNumericRelation)
    assert isinstance(merged_count, mv.MaterializedRolledNumericRelation)
    expected_count = sum(
        str(order[4]).startswith("2026-08") for order in analysis_dsl_rows(scenario).orders
    )
    assert total_count.to_pandas()["value"].tolist() == [expected_count]
    assert merged_count.to_pandas()["value"].tolist() == [expected_count]

    identifier = {
        "change": "selected-next-month",
        "ratio_routes": "multi-root-ratio",
        "order_count": "customer-association",
    }[part]
    selected = _example("en", identifier)
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
    code = _example("en", "mean-original-rollup")
    exec(compile(code, "mean-rollup-example", "exec"), namespace)
    overall = namespace["overall"]
    assert isinstance(overall, mv.MaterializedRolledNumericRelation)
    assert overall.to_pandas()["value"].tolist() == pytest.approx([1000 / 3])


@pytest.mark.runtime
@pytest.mark.parametrize("example", ("monthly", "regional", "collection"))
def test_semantic_monthly_observation_example_executes(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch, example: str
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    model = authoring_evidence_project / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        .replace("name='log_date', entity=orders", "name='order_date', entity=orders")
        .replace("Asia/Shanghai", "UTC")
        .replace("parse=ms.strptime('%Y%m%d')", "parse=None")
    )
    with duckdb.connect(str(authoring_evidence_project / "warehouse.duckdb")) as connection:
        connection.execute("ALTER TABLE orders ALTER COLUMN query_id TYPE BIGINT")
        identifiers = connection.execute("SELECT query_id FROM orders ORDER BY query_id").fetchall()
        assert len(identifiers) == 4
        dates = (
            ("20260101", "20260201", "20260301", "20260401")
            if example == "monthly"
            else ("20261001", "20261101", "20261201", "20270101")
        )
        for identifier, day, amount in zip(
            identifiers,
            dates,
            (10, 20, 30, 40),
            strict=True,
        ):
            connection.execute(
                "UPDATE orders SET log_date=?, amount=? WHERE query_id=?",
                [day, amount, identifier[0]],
            )
    with duckdb.connect(str(authoring_evidence_project / "warehouse.duckdb")) as connection:
        connection.execute(
            "ALTER TABLE orders ALTER COLUMN log_date TYPE DATE USING strptime(log_date, '%Y%m%d')::DATE"
        )
    session = mv.session.get_or_create("semantic-example", report_timezone="UTC")
    namespace: dict[str, object] = {
        "mv": mv,
        "ms": ms,
        "session": session,
        "catalog": session.catalog,
    }
    identifier = {
        "monthly": "catalog-monthly-observation",
        "regional": "catalog-regional-observation",
        "collection": "catalog-discovery",
    }[example]
    code = _example("en", identifier)
    exec(compile(code, "semantic-observation-example", "exec"), namespace)
    logical = namespace["dataset" if example == "monthly" else "current"]
    assert isinstance(logical, mv.LogicalNumericRelation)
    assert session.runs().items == ()
    assert not session._runtime.statistics.statements
    rows = logical.execute().to_pandas()
    if example == "regional":
        assert not rows.duplicated(["group", "coord_0"]).any()
        assert len(rows) == 6
        positive = rows.loc[rows["value"] > 0]
        assert set(
            zip(positive["coord_0"].str[:7], positive["group"], positive["value"], strict=True)
        ) == {
            ("2026-10", "moon-base", 10),
            ("2026-11", "orbital", 20),
            ("2026-12", "moon-base", 30),
        }
    else:
        assert rows["value"].tolist() == [10, 20, 30]
        assert len(rows) == 3


@pytest.mark.runtime
def test_workflow_evidence_and_cold_recovery_examples(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    namespace: dict[str, object] = {}
    exec(compile(_example("en", "customer-period-change"), "workflow-example", "exec"), namespace)
    change = namespace["change"]
    assert isinstance(change, mv.MaterializedDifferenceRelation)
    expected = {"A": -40, "B": 20, "C": -50, "D": 0}
    assert change.to_pandas().set_index("member")["value"].to_dict() == expected
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    assert len(session.runs().items) == 1
    exec(compile(_example("en", "evidence-producing-run"), "evidence-example", "exec"), namespace)
    # Repeated source definitions must create a new evaluation and immutable Artifact.
    assert len(session.runs().items) == 2
    second = namespace["artifact"]
    assert isinstance(second, mv.MaterializedDifferenceRelation)
    assert second.state.artifact_ref != change.state.artifact_ref
    case.database_path.unlink()
    namespace["session"] = mv.session.resume(session.id, by="id")
    namespace["run_id"] = change.state.producing_run_ref
    exec(compile(_example("en", "evidence-recovery"), "recovery-example", "exec"), namespace)
    recovered = namespace["artifact"]
    assert isinstance(recovered, mv.MaterializedDifferenceRelation)
    assert recovered.state.artifact_ref == change.state.artifact_ref
    assert recovered.to_pandas().set_index("member")["value"].to_dict() == expected
    assert len(session.runs().items) == 2


@pytest.mark.runtime
def test_coordinate_row_statistic_workflow_example(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    namespace: dict[str, object] = {"session": case.session, "mv": mv, "ms": ms}
    block = _example("en", "current-row-statistics")
    exec(compile(block, "coordinate-example", "exec"), namespace)
    counts = namespace["row_counts"]
    category_count = namespace["category_count"]
    assert isinstance(counts, mv.MaterializedStatisticRelation)
    assert isinstance(category_count, mv.MaterializedStatisticRelation)
    assert counts.to_pandas().set_index("group")["value"].to_dict() == {"east": 2}
    assert category_count.to_pandas()["value"].tolist() == [2]
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
            by=(ms.ref.entity("sales.customer"),),
        ),
    }
    code = _example("en", "ranking-table")
    exec(compile(code, "display-example", "exec"), namespace)
    result = namespace["result"]
    assert isinstance(result, mv.MaterializedTable)
    assert result.to_pandas().columns.tolist() == ["member", "amount", "rank"]
    restored = namespace["restored"]
    assert isinstance(restored, mv.MaterializedTable)
    assert result.to_pandas().equals(restored.to_pandas())


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
    code = _example("en", "absolute-attribution")
    assert code == _example("zh", "absolute-attribution")
    exec(compile(code, "attribution-example", "exec"), namespace)
    allocation = namespace["allocation"]
    restored = namespace["restored_allocation"]
    table = namespace["allocated_table"]
    assert isinstance(allocation, mv.MaterializedAttributionResult)
    assert isinstance(restored, mv.MaterializedAttributionResult)
    assert isinstance(table, mv.MaterializedTable)
    assert allocation.contribution.to_pandas().equals(restored.contribution.to_pandas())
    assert table.to_pandas()["contribution"].sum() == 0
    facts = analysis_dsl_rows("j2")
    rows = table.to_pandas()
    for side, month in (("current", "2026-08"), ("baseline", "2026-07")):
        assert rows[side].sum() == sum(str(order[4]).startswith(month) for order in facts.orders)
    recovered_table = case.session.artifact(table.artifact_ref)
    assert isinstance(recovered_table, mv.MaterializedTable)
    assert recovered_table.to_pandas().equals(rows)


@pytest.mark.runtime
@pytest.mark.parametrize("scoped", (False, True))
def test_scalar_channel_workflow_examples_execute(
    analysis_dsl_case_factory: DslCaseFactory, scoped: bool
) -> None:
    from dataclasses import replace

    from tests.shared_fixtures import DSL_NAMES

    case = analysis_dsl_case_factory("j1", names=replace(DSL_NAMES, order="orders"))
    code = _example(
        "en", "scoped-channel-observation" if scoped else "unscoped-channel-observation"
    )
    namespace: dict[str, object] = {"session": case.session, "mv": mv, "ms": ms}
    exec(compile(code, "channel-ranking-example", "exec"), namespace)
    by_channel = namespace["by_channel"]
    assert isinstance(by_channel, mv.LogicalNumericRelation)
    rows = by_channel.execute().to_pandas()
    expected = {"web": 77} if scoped else {"web": 450 + 400 + 77, "mobile": 150 + 99}
    assert rows.set_index("group")["value"].dropna().to_dict() == expected


@pytest.mark.runtime
def test_hourly_and_daily_source_workflow_examples_execute(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import replace

    from tests.shared_fixtures import DSL_NAMES

    case = analysis_dsl_case_factory("j2", names=replace(DSL_NAMES, order="orders"))
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE orders SET ordered_at=TIMESTAMPTZ '2026-07-01 00:00:00+00:00'")
    code = _example("en", "hourly-daily-source")
    namespace: dict[str, object] = {"session": case.session, "mv": mv, "ms": ms}
    exec(compile(code, "hourly-daily-source-example", "exec"), namespace)
    hourly = namespace["orders_by_hour"]
    assert isinstance(hourly, mv.MaterializedNumericRelation)
    hourly_rows = hourly.to_pandas()
    daily_run = case.session.runs().items[0]
    assert isinstance(daily_run, mv.SucceededRun)
    daily = case.session.artifact(daily_run.output_artifact_ref)
    assert isinstance(daily, mv.MaterializedNumericRelation)
    daily_rows = daily.to_pandas()
    assert len(hourly_rows) == 48 and len(daily_rows) == 2
    expected = 100 + 100 + 50 + 0 + 60 + 120 + 0 + 0 + 30 + 200 + 0
    assert hourly_rows.loc[hourly_rows["value"] > 0, "value"].tolist() == [expected]
    assert daily_rows.loc[daily_rows["value"] > 0, "value"].tolist() == [expected]
    assert (
        hourly_rows.iloc[0]["group"] == daily_rows.iloc[0]["group"] == "2026-07-01T00:00:00+00:00"
    )
