"""Full opportunity cohort decisions with independent counts."""

import pytest

import marivo.analysis as mv
from marivo.analysis.dsl.column import row_count
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, catalog_value, terminal_runs
from tests.support.paths import PROJECT_ROOT


def test_full_entity_time_cohort(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    session = case.session
    targets = session.members(session.catalog.get("entity:sales.customer").value)
    grid = session.time_scope(start="2026-07-01", end="2026-10-01").by(mv.grain("month"))
    values = session.observe(
        {"value": session.catalog.get("metric:sales.order_count").value},
        by=mv.product(targets, grid),
    )
    active = values.where(values["value"] > 0)
    counts = active.reduce({"months": row_count()}, by=(active.axes[0],))
    result = counts.where(counts["months"] >= 2).compute()
    months: dict[str, set[str]] = {}
    for _, member, _, _, occurred, _ in analysis_dsl_rows("j2").orders:
        if member is not None and "2026-07" <= str(occurred)[:7] < "2026-10":
            months.setdefault(member, set()).add(str(occurred)[:7])
    expected = {member for member, active in months.items() if len(active) >= 2}
    assert set(result.to_pandas().iloc[:, 0]) == expected
    fixed_values = values.compute()

    def forbid_source_read(*args: object, **kwargs: object) -> None:
        pytest.fail("Pandas full-opportunity cohort must use exported values only")

    with monkeypatch.context() as fixed_only:
        fixed_only.setattr(SourceSession, "execute", forbid_source_read)
        frame = fixed_values.to_pandas()
        assert len(frame) == len(analysis_dsl_rows("j2").customers) * 3
        member_column = frame.columns[0]
        mask = frame["value"] > 0
        active_months = frame.loc[mask].groupby(member_column).size()
        assert set(active_months.loc[active_months >= 2].index) == expected


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
            "INSERT INTO usage_sessions VALUES (1, 'A', '2026-08-03', 8), (2, 'A', '2026-08-04', 14), (3, 'B', '2026-08-03', 9), (4, 'B', '2026-08-04', 11), (5, 'C', '2026-08-03', 12), (6, 'C', '2026-08-04', 12), (7, 'D', '2026-08-03', 1), (8, 'B', '2026-08-10', 1000)"
        )
        database.execute(
            "CREATE TABLE purchases (id BIGINT, customer_id VARCHAR, happened TIMESTAMP, amount BIGINT)"
        )
        database.execute(
            "INSERT INTO purchases VALUES (1, 'A', '2026-08-10', 100), (2, 'B', '2026-08-10', 200), (3, 'C', '2026-08-10', 300), (4, 'D', '2026-08-10', 400), (5, 'A', '2026-08-03', 900)"
        )
    model = case.root / "models/semantic/sales/models.py"
    original = model.read_text()
    capture_end = original.index("# Named expressions")
    declarations = """
usage_sessions = ms.entity(name='usage_sessions', datasource=warehouse, source=md.table('usage_sessions'), primary_key=['id'])
usage_user = ms.dimension_column(name='customer_id', entity=usage_sessions, column='customer_id')
usage_time = ms.time_dimension_column(name='happened', entity=usage_sessions, column='happened', parse=ms.timestamp(timezone='UTC'))
usage_hours = ms.measure_column(name='hours', entity=usage_sessions, column='hours', additivity=ms.additive_all(), unit='hour')
usage_to_user = ms.relationship(name='usage_to_user', from_entity=usage_sessions, to_entity=customer, keys=[ms.join_on(usage_user, customer_id)])
purchases = ms.entity(name='purchases', datasource=warehouse, source=md.table('purchases'), primary_key=['id'])
purchase_user = ms.dimension_column(name='customer_id', entity=purchases, column='customer_id')
purchase_time = ms.time_dimension_column(name='happened', entity=purchases, column='happened', parse=ms.timestamp(timezone='UTC'))
purchase_amount = ms.measure_column(name='amount', entity=purchases, column='amount', additivity=ms.additive_all(), unit='CNY')
purchase_to_user = ms.relationship(name='purchase_to_user', from_entity=purchases, to_entity=customer, keys=[ms.join_on(purchase_user, customer_id)])
"""
    model.write_text(
        original[:capture_end]
        + declarations
        + original[capture_end:]
        + """
usage_value = usage_hours
usage_time_value = usage_time
purchase_value = purchase_amount
purchase_time_value = purchase_time
@ms.metric()
def avg_usage_hours():
    return usage_value.mean(time=usage_time_value)
@ms.metric()
def spending_amount():
    return purchase_value.sum(time=purchase_time_value)
"""
    )
    session = case.session
    users = session.members(session.catalog.get("entity:sales.customer").value)
    usage = session.observe(
        {"value": session.catalog.get("metric:sales.avg_usage_hours").value},
        by=mv.product(session.time_scope(start="2026-08-03", end="2026-08-10"), users),
    )
    chosen = usage.where(usage["value"] > 10).compute()
    eligible = session.population_from(
        chosen, on=((chosen.axes[1], session.catalog.get("entity:sales.customer").value),)
    )
    spending = session.observe(
        {"value": session.catalog.get("metric:sales.spending_amount").value},
        by=mv.product(session.time_scope(start="2026-08-10", end="2026-08-17"), eligible),
        where=mv.in_population(eligible),
    )
    assert terminal_runs(session) == ()
    frame = spending.compute().to_pandas()
    assert frame.set_index("sales.customer")["value"].to_dict() == {"A": 100, "C": 300}


@pytest.mark.release
@pytest.mark.slow
def test_cold_full_opportunity_exports(analysis_dsl_case_factory: DslCaseFactory) -> None:
    import os
    import subprocess
    import sys

    case = analysis_dsl_case_factory("j2")
    session = case.session
    targets = session.members(session.catalog.get("entity:sales.customer").value)
    grid = session.time_scope(start="2026-07-01", end="2026-10-01").by(mv.grain("month"))
    values = session.observe(
        {"value": session.catalog.get("metric:sales.order_count").value},
        by=mv.product(targets, grid),
    )
    active = values.where(values["value"] > 0)
    counts = active.reduce({"count": row_count()}, by=(active.axes[0],))
    accepted = counts.where(counts["count"] >= 1)
    saved_targets, saved_values, result = (
        session.frame(targets).persist(),
        values.persist(),
        accepted.persist(),
    )
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("exported cohort cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
session = mv.session.resume(sys.argv[1])
targets, values, saved = [session.persisted_frame(ref) for ref in sys.argv[2:]]
frame = values.to_pandas()
assert len(frame) == len(targets.to_pandas()) * 3
member = frame.columns[0]
active = frame.loc[frame["value"] > 0].groupby(member).size()
assert set(active.loc[active >= 1].index) == set(saved.to_pandas().iloc[:, 0])
assert len(terminal_runs(session)) == 3
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                session.id,
                saved_targets.artifact.id,
                saved_values.artifact.id,
                result.artifact.id,
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


def test_no_time_selection_preserves_complete_target_decisions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    targets = session.members(session.catalog.get("entity:sales.customer").value)
    category = session.read(
        targets, {"region": catalog_value(session.catalog, "sales.customer.region", kind="field")}
    )
    expected = {member for member, region in analysis_dsl_rows("j2").customers if region == "east"}
    selected = category.where(~~(category["region"] == "east")).compute()
    assert set(selected.to_pandas().iloc[:, 0]) == expected
    frame = category.compute().to_pandas()
    assert len(frame) == len(analysis_dsl_rows("j2").customers)
    accepted = frame["region"] == "east"
    assert set(frame.loc[accepted].iloc[:, 0]) == expected
    assert accepted.tolist() == [True, True, False, False]
