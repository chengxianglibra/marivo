"""Full opportunity cohort decisions with independent counts."""

from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._cohort import decide
from marivo.analysis.materialization.errors import MaterializationError
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows
from tests.support.paths import PROJECT_ROOT


def test_quantifier_decisions() -> None:
    cases: tuple[tuple[Literal["any", "at_least", "all"], int, int, int, int, bool | None], ...] = (
        ("at_least", 3, 3, 1, 0, True),
        ("at_least", 3, 1, 1, 2, False),
        ("at_least", 3, 2, 1, 1, None),
        ("any", 1, 1, 1, 0, True),
        ("any", 1, 0, 1, 1, None),
        ("all", 1, 1, 1, 1, False),
        ("all", 1, 2, 0, 0, True),
    )
    for rule, k, true_count, unknown_count, false_count, expected in cases:
        assert decide(rule, k, "false", true_count, unknown_count, false_count) is expected, (
            rule,
            k,
            true_count,
            unknown_count,
            false_count,
        )

    empty_cases: tuple[tuple[Literal["true", "false", "undefined"], bool | None], ...] = (
        ("true", True),
        ("false", False),
        ("undefined", None),
    )
    for empty, expected in empty_cases:
        assert decide("all", 1, empty, 0, 0, 0) is expected
        assert decide("any", 1, empty, 0, 0, 0) is False
        assert decide("at_least", 3, empty, 0, 0, 0) is False


def test_full_entity_time_cohort(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j2")
    n = case.names
    targets = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    grid = mv.time_grid(
        during=mv.time_scope(
            start="2026-07-01",
            end="2026-10-01",
        ),
        grain=mv.grain("month"),
        timezone="UTC",
    )
    values = targets.observe(
        ms.ref.metric(f"{n.domain}.{n.order_count}"),
        during=grid,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    assert isinstance(values, mv.LogicalNumericRelation)
    facts = analysis_dsl_rows("j2")
    months: dict[str, set[str]] = {}
    for _, member, _, _, occurred, _ in facts.orders:
        if member is not None and "2026-07" <= str(occurred)[:7] < "2026-10":
            months.setdefault(member, set()).add(str(occurred)[:7])
    expected = {member for member, active in months.items() if len(active) >= 2}
    result = targets.cohort(values.value.gt(0), rule=mv.at_least(2)).execute()
    assert set(result.to_pandas().member) == expected
    fixed_targets, fixed_values = targets.execute(), values.execute()
    from marivo.datasource.adapters import SourceSession

    def forbid_source_read(*args: object, **kwargs: object) -> None:
        pytest.fail("Fixed full-opportunity cohort must use retained artifacts only")

    with monkeypatch.context() as fixed_only:
        fixed_only.setattr(SourceSession, "batches", forbid_source_read)
        fixed = fixed_targets.cohort(fixed_values.value.gt(0), rule=mv.at_least(2)).execute()
    assert set(fixed.to_pandas().member) == expected
    with pytest.raises(MaterializationError) as incomplete:
        targets.cohort(
            values.where(values.value.gt(0)).value.gt(0), rule=mv.any_instance()
        ).execute()
    assert incomplete.value.expected == "complete_coverage"
    assert incomplete.value.stage == "graph_check"


def test_previous_week_mean_selects_users_for_current_week_spending(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute(
            "CREATE TABLE usage_sessions (id BIGINT, customer_id VARCHAR, happened TIMESTAMP, hours BIGINT)"
        )
        database.execute(
            "INSERT INTO usage_sessions VALUES "
            "(1, 'A', '2026-08-03', 8), (2, 'A', '2026-08-04', 14), "
            "(3, 'B', '2026-08-03', 9), (4, 'B', '2026-08-04', 11), "
            "(5, 'C', '2026-08-03', 12), (6, 'C', '2026-08-04', 12), "
            "(7, 'D', '2026-08-03', 1), (8, 'B', '2026-08-10', 1000)"
        )
        database.execute(
            "CREATE TABLE purchases (id BIGINT, customer_id VARCHAR, happened TIMESTAMP, amount BIGINT)"
        )
        database.execute(
            "INSERT INTO purchases VALUES "
            "(1, 'A', '2026-08-10', 100), (2, 'B', '2026-08-10', 200), "
            "(3, 'C', '2026-08-10', 300), (4, 'D', '2026-08-10', 400), "
            "(5, 'A', '2026-08-03', 900)"
        )
    model = case.root / "models" / "semantic" / "sales" / "models.py"
    model.write_text(
        model.read_text()
        + "\nusage_sessions = ms.entity(name='usage_sessions', datasource=warehouse, source=md.table('usage_sessions'), primary_key=['id'])\n"
        "usage_user = ms.dimension_column(name='customer_id', entity=usage_sessions, column='customer_id')\n"
        "usage_time = ms.time_dimension_column(name='happened', entity=usage_sessions, column='happened', granularity='day', parse=ms.timestamp(timezone='UTC'))\n"
        "usage_hours = ms.measure_column(name='hours', entity=usage_sessions, column='hours', additivity=ms.additive_all(), unit='hour')\n"
        "avg_usage_hours = ms.aggregate(name='avg_usage_hours', measure=usage_hours, agg='mean', time=usage_time)\n"
        "usage_to_user = ms.relationship(name='usage_to_user', from_entity=usage_sessions, to_entity=customer, keys=[ms.join_on(usage_user, customer_id)])\n"
        "purchases = ms.entity(name='purchases', datasource=warehouse, source=md.table('purchases'), primary_key=['id'])\n"
        "purchase_user = ms.dimension_column(name='customer_id', entity=purchases, column='customer_id')\n"
        "purchase_time = ms.time_dimension_column(name='happened', entity=purchases, column='happened', granularity='day', parse=ms.timestamp(timezone='UTC'))\n"
        "purchase_amount = ms.measure_column(name='amount', entity=purchases, column='amount', additivity=ms.additive_all(), unit='CNY')\n"
        "spending_amount = ms.aggregate(name='spending_amount', measure=purchase_amount, agg='sum', time=purchase_time)\n"
        "purchase_to_user = ms.relationship(name='purchase_to_user', from_entity=purchases, to_entity=customer, keys=[ms.join_on(purchase_user, customer_id)])\n"
    )
    ms.load(workspace_dir=case.root)
    user = ms.ref.entity("sales.customer")
    users = case.session.members(user)
    usage = users.observe(
        ms.ref.metric("sales.avg_usage_hours"),
        during=mv.time_scope(start="2026-08-03", end="2026-08-10"),
        via=ms.ref.relationship("sales.usage_to_user"),
        by=(mv.member(),),
    )
    assert isinstance(usage, mv.LogicalNumericRelation)
    eligible = usage.where(usage.value.gt(10)).members()
    assert isinstance(eligible, mv.LogicalAnalysisDomain)
    current_week = mv.time_scope(start="2026-08-10", end="2026-08-17")
    spending = eligible.observe(
        ms.ref.metric("sales.spending_amount"),
        during=current_week,
        via=ms.ref.relationship("sales.purchase_to_user"),
        by=(mv.member(),),
    )
    assert case.session.runs().items == ()
    frame = spending.execute().to_pandas()
    assert frame.set_index("member")["value"].to_dict() == {"A": 100, "C": 300}


@pytest.mark.slow
def test_cold_full_opportunity_continuation(analysis_dsl_case_factory: DslCaseFactory) -> None:
    import os
    import subprocess
    import sys

    case = analysis_dsl_case_factory("j2")
    n = case.names
    targets = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-07-01", end="2026-10-01"), grain=mv.grain("month")
    )
    values = targets.observe(
        ms.ref.metric(f"{n.domain}.{n.order_count}"),
        during=grid,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    saved_targets, saved_values = targets.execute(), values.execute()
    result = saved_targets.cohort(saved_values.value.gt(0), rule=mv.any_instance()).execute()
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("fixed cohort cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
session = mv.session.resume(sys.argv[1], by="id")
targets, values, saved = [session.artifact(ref) for ref in sys.argv[2:]]
result = targets.cohort(values.value.gt(0), rule=mv.any_instance(), through=values.subject_binding).execute()
assert result.to_pandas().equals(saved.to_pandas())
selected = values.where(values.value.is_defined())
assert len(selected.where(selected.value.gte(0)).members().execute().to_pandas()) == len(targets.to_pandas())
assert "cohort_decision" in {part.role for part in saved._dataset.verified().parts}
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                case.session.id,
                saved_targets.state.artifact_ref.ref,
                saved_values.state.artifact_ref.ref,
                result.state.artifact_ref.ref,
            ],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        offline.rename(case.database_path)
    assert completed.returncode == 0, completed.stderr


def test_no_time_quantifiers_and_complete_target_decisions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    targets = case.session.members(ms.ref.entity("sales.customer"))
    category = targets.read(ms.ref.dimension("sales.customer.region"))
    assert isinstance(category, mv.LogicalCategoryRelation)
    expected = {member for member, region in analysis_dsl_rows("j2").customers if region == "east"}
    source = (targets, category, mv.any_instance())
    fixed = (
        targets.execute(),
        category.execute(),
        mv.all_instances(empty=mv.empty_opportunity.undefined()),
    )
    for target, values, rule in (source, fixed):
        result = target.cohort(mv.not_(mv.not_(values.value.eq("east"))), rule=rule).execute()
        assert set(result.to_pandas().member) == expected
        assert result._dataset is not None
        decisions = next(
            part.table
            for part in result._dataset.verified().parts
            if part.role == "cohort_decision"
        )
        assert len(decisions) == len(analysis_dsl_rows("j2").customers)
        assert {
            row["key_0"] for row in decisions.to_pylist() if row["cohort_decision__accepted"]
        } == expected
