"""Public source and fixed comparison journeys with independent raw-fact oracles."""

from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models

_NUMERIC_SOURCES = [
    (physical, parquet)
    for physical in ("BIGINT", "DOUBLE", "DECIMAL(30,6)", "duration")
    for parquet in (False, True)
] + [("duration_" + unit, True) for unit in ("s", "ms", "ns")]


def _numeric_source(case: DslCase, physical: str, parquet: bool) -> None:
    with duckdb.connect(str(case.database_path)) as connection:
        if physical.startswith("duration"):
            if not parquet:
                connection.execute(
                    'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
                )
        else:
            connection.execute(f'ALTER TABLE "order" ALTER amount TYPE {physical}')
    if parquet:
        export_dsl_parquet_models(case, case.root)
        if physical.startswith("duration"):
            path = case.root / "source_files/order.parquet"
            data = pq.read_table(path)
            data = data.set_column(
                data.schema.get_field_index("amount"),
                "amount",
                data["amount"].cast(pa.duration(physical.partition("_")[2] or "us")),
            )
            pq.write_table(data, path)
    ms.load(workspace_dir=case.root)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "physical,parquet",
    [
        (physical, parquet)
        for physical in ("BIGINT", "DOUBLE", "DECIMAL(30,6)", "duration")
        for parquet in (False, True)
    ]
    + [("duration_" + unit, True) for unit in ("s", "ms", "ns")],
)
def test_public_numeric_difference_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory, physical: str, parquet: bool
) -> None:
    unit = physical.partition("_")[2] or "us"
    case = analysis_dsl_case_factory("j2")
    names = case.names
    with duckdb.connect(str(case.database_path)) as connection:
        if not physical.startswith("duration"):
            connection.execute(
                f'ALTER TABLE "{names.order}" ALTER "{names.amount}" TYPE {physical}'
            )
        raw = connection.execute(
            f'SELECT "{names.customer_id}", "{names.ordered_at}", "{names.amount}" FROM "{names.order}"'
        ).fetchall()
        if physical.startswith("duration") and not parquet:
            connection.execute(
                f'ALTER TABLE "{names.order}" ALTER "{names.amount}" TYPE INTERVAL USING to_microseconds("{names.amount}")'
            )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        if physical.startswith("duration"):
            path = case.root / "source_files/order.parquet"
            data = pq.read_table(path)
            pq.write_table(
                data.set_column(
                    data.schema.get_field_index(names.amount),
                    names.amount,
                    data[names.amount].cast(pa.duration(unit)),
                ),
                path,
            )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    metric = ms.ref.metric(f"{names.domain}.{names.revenue}")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    current = members.observe(
        metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=buyer
    )
    baseline = members.observe(
        metric, during=mv.time_scope(start="2026-07-01", end="2026-08-01"), via=buyer
    )
    source = current.compare(baseline).execute().to_pandas().set_index("member")
    first, second = current.execute(), baseline.execute()
    fixed = first.compare(second).execute().to_pandas().set_index("member")
    expected: dict[str, Fraction] = {}
    for member, occurred, amount in raw:
        month = str(occurred)[:7]
        if month in ("2026-07", "2026-08"):
            expected[member] = expected.get(member, Fraction(0)) + Fraction(amount) * (
                1 if month == "2026-08" else -1
            )
    if physical.startswith("duration"):
        for frame in (source, fixed):
            frame["value"] = (
                pa.array(frame["value"]).cast(pa.duration(unit)).cast(pa.int64()).to_pylist()
            )
    assert source["value"].to_dict() == expected
    assert fixed["value"].to_dict() == expected
    assert source["cell_tag"].eq("defined").all()
    assert fixed["cell_tag"].eq("defined").all()
    if physical.startswith("DECIMAL"):
        assert all(type(value) is Decimal for value in fixed["value"])


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_comparison_v2_cold_continuation_and_correspondence_integrity(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    from dataclasses import replace

    from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow
    from marivo.analysis.materialization.graph_protocol import validate_descriptor

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    current = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    baseline = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    saved = current.compare(baseline).execute()
    dataset = saved._dataset
    assert dataset is not None
    descriptor = dataset.artifact.descriptor
    assert descriptor.method_state.contract_version == 2
    assert all(part.contract_version == part.method_state_version == 2 for part in descriptor.parts)
    with pytest.raises(IntegrityError):
        validate_descriptor(
            replace(
                descriptor,
                parts=tuple(replace(part, contract_version=1) for part in descriptor.parts),
            )
        )
    checked = dataset.verified()
    mapping = next(part.table for part in checked.parts if part.role == "correspondence")
    corrupted = mapping.set_column(
        mapping.schema.get_field_index("correspondence__baseline_present"),
        "correspondence__baseline_present",
        pa.array([False] * mapping.num_rows),
    )
    with pytest.raises(MaterializationError, match="correspondence"):
        from_arrow(
            checked.primary,
            checked.contract,
            parts=tuple(
                ExchangePart(part.role, corrupted) if part.role == "correspondence" else part
                for part in checked.parts
            ),
            completed_checks=checked.completed_checks,
            method_state=checked.method_state,
        )
    incomplete_empty = mapping.drop(["correspondence__current_present"]).slice(0)
    assert checked.method_state is not None
    with pytest.raises(MaterializationError, match="correspondence schema"):
        from_arrow(
            checked.primary.slice(0),
            replace(
                checked.contract,
                parts=tuple(
                    replace(part, schema=incomplete_empty.schema)
                    if part.role == "correspondence"
                    else part
                    for part in checked.contract.parts
                ),
            ),
            parts=tuple(
                ExchangePart(
                    part.role,
                    incomplete_empty if part.role == "correspondence" else part.table.slice(0),
                )
                for part in checked.parts
            ),
            completed_checks=checked.completed_checks,
            method_state=checked.method_state.slice(0),
        )
    source_path = case.root / "source_files" if parquet else case.database_path
    offline = source_path.with_name(source_path.name + ".offline")
    source_path.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("cold comparison cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
saved = mv.session.resume(sys.argv[1], by="id").artifact(sys.argv[2])
assert isinstance(saved, mv.MaterializedDifferenceRelation)
selected = saved.where(saved.value.gt(-1)).execute()
assert selected.to_pandas().set_index("member")["value"].to_dict() == {"B": 20, "D": 0}
assert selected._dataset.artifact.descriptor.method_state.contract_version == 2
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, case.session.id, saved.state.artifact_ref.ref],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    finally:
        offline.rename(source_path)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_nested_difference_keeps_independent_captures(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    from marivo.analysis.materialization.graph_composition import comparison_endpoints

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                (f"june_{member}", member, "web", "paid", "2026-06-10T12:00:00+00:00", 0)
                for member in ("A", "B", "C", "D")
            ],
        )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))

    def observe(month: int) -> mv.LogicalNumericRelation:
        result = members.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        assert isinstance(result, mv.LogicalNumericRelation)
        return result

    august, july_first, july_second, june = observe(8), observe(7), observe(7), observe(6)
    first, second = august.compare(july_first), july_second.compare(june)
    nested = first.compare(second)
    definition = nested._node.definition
    inner = comparison_endpoints(definition)
    assert comparison_endpoints(inner[0])[1].identity != comparison_endpoints(inner[1])[0].identity
    expected = {"A": -140, "B": -80, "C": -100, "D": 0}
    assert nested.execute().to_pandas().set_index("member")["value"].to_dict() == expected
    fixed_first, fixed_second = first.execute(), second.execute()
    fixed = fixed_first.compare(fixed_second).execute()
    assert fixed.to_pandas().set_index("member")["value"].to_dict() == expected
    assert "compare" in {
        item.call.split("(")[0].removeprefix("relation.") for item in fixed.contract().actions
    }
    assert not hasattr(fixed, "rollup")
    assert (
        comparison_endpoints(comparison_endpoints(fixed._node.definition)[0])[1].identity
        == july_first._node.root.identity
    )

    # The retained nested endpoints must survive a fresh process without any source access.
    from marivo.analysis.materialization.graph_protocol import freeze_graph

    assert fixed._dataset is not None
    verified = fixed._dataset.verified()
    (case.root / "expected-definition.txt").write_text(freeze_graph(fixed._node.definition))
    for name, table in [
        ("primary", verified.primary),
        *((part.role, part.table) for part in verified.parts),
    ]:
        with (
            pa.OSFile(str(case.root / f"expected-{name}.arrow"), "wb") as sink,
            pa.ipc.new_file(sink, table.schema) as writer,
        ):
            writer.write_table(table)
    source_path = case.root / "source_files" if parquet else case.database_path
    offline = source_path.with_name(source_path.name + ".offline")
    source_path.rename(offline)
    script = """
import sys
from pathlib import Path
import duckdb
import pyarrow as pa
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.analysis.materialization.graph_protocol import freeze_graph
from marivo.analysis.materialization.graph_composition import comparison_endpoints

def unavailable(*args, **kwargs):
    raise AssertionError("nested cold recovery cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
SourceSession.stage_derived = unavailable
saved = mv.session.resume(sys.argv[1], by="id").artifact(sys.argv[2])
assert freeze_graph(saved._node.definition) == Path("expected-definition.txt").read_text()
first, second = comparison_endpoints(saved._node.definition)
assert comparison_endpoints(first)[1].identity != comparison_endpoints(second)[0].identity
checked = saved._dataset.verified()
for name, table in [("primary", checked.primary), *((part.role, part.table) for part in checked.parts)]:
    with pa.memory_map(f"expected-{name}.arrow", "r") as source:
        assert table.equals(pa.ipc.open_file(source).read_all())
assert not hasattr(saved, "rollup")
selected = saved.where(saved.value.lt(0)).execute()
assert selected.to_pandas().set_index("member")["value"].to_dict() == {"A": -140, "B": -80, "C": -100}
assert selected._dataset.verified().contract.signature.quantity == checked.contract.signature.quantity
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, case.session.id, fixed.state.artifact_ref.ref],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    finally:
        offline.rename(source_path)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.runtime
@pytest.mark.parametrize("physical,parquet", _NUMERIC_SOURCES)
def test_relative_change_exact_finish_and_zero_baseline(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool, physical: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, physical, parquet)
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales.order_buyer")
    current = members.observe(
        metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=via
    )
    baseline = members.observe(
        metric, during=mv.time_scope(start="2026-07-01", end="2026-08-01"), via=via
    )
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        saved = left.compare(right, value="relative_change").execute()
        frame = saved.to_pandas().set_index("member")
        assert float(frame.loc["A", "value"]) == -0.4
        assert float(frame.loc["B", "value"]) == 0.2
        assert float(frame.loc["C", "value"]) == -1.0
        assert frame.loc["D", "cell_tag"] == "undefined"
        assert frame.loc["D", "cell_reason"] == "zero_baseline"
        assert saved._dataset is not None
        assert saved._dataset.artifact.descriptor.method_state.contract_version == 1


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_ordinary_ratio_and_cohort_singleton(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales.order_buyer")
    current = members.observe(
        metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=via
    )
    assert isinstance(current, mv.LogicalNumericRelation)
    for left in (current, current.execute()):
        result = left.ratio(left).execute()
        frame = result.to_pandas().set_index("member")
        assert frame.loc["A", "value"] == 1.0
        assert frame.loc["B", "value"] == 1.0
        assert frame.loc["C", "cell_reason"] == "zero_denominator"
        assert frame.loc["D", "cell_reason"] == "zero_denominator"
        assert "rollup" not in {
            action.call.split("(")[0].removeprefix("relation.")
            for action in result.contract().actions
        }
    first = current.rollup()
    second_members = case.session.members(ms.ref.entity("sales.customer"))
    second_observed = second_members.observe(
        metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=via
    )
    second = second_observed.rollup()
    for left, right in ((first, second), (first.execute(), second.execute())):
        result = left.compare(right, design=mv.CohortContrast()).execute()
        assert result.to_pandas()["value"].tolist() == [0]


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_union_keep_distinguishes_missing_coordinate_and_rejects_filtered_empty(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales.order_buyer")
    current = members.observe(
        metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=via
    )
    baseline = members.observe(
        metric, during=mv.time_scope(start="2026-07-01", end="2026-08-01"), via=via
    )
    assert isinstance(current, mv.LogicalNumericRelation)
    assert isinstance(baseline, mv.LogicalNumericRelation)
    current_selected = current.where(current.value.gt(0))
    baseline_selected = baseline.where(baseline.value.gt(0))
    design = mv.TimeChange(pairing=mv.UnionKeys(missing="keep"))
    for left, right in (
        (current_selected, baseline_selected),
        (current_selected.execute(), baseline_selected.execute()),
    ):
        result = left.compare(right, design=design).execute()
        frame = result.to_pandas().set_index("member")
        assert frame.loc["A", "value"] == -40
        assert frame.loc["B", "value"] == 20
        assert frame.loc["C", "cell_tag"] == "undefined"
        assert frame.loc["C", "cell_reason"] == "missing_side"
        assert result._dataset is not None
        checked = result._dataset.verified()
        correspondence = next(
            part.table for part in checked.parts if part.role == "correspondence"
        ).to_pylist()
        missing = next(row for row in correspondence if row["key_0"] == "C")
        assert missing["correspondence__current_present"] is False
        assert missing["correspondence__baseline_present"] is True
        with pytest.raises(AnalysisError, match="complete raw observations"):
            left.compare(right, design=mv.TimeChange(pairing=mv.UnionKeys(missing="metric_empty")))


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize("metric_name", ["revenue", "order_count", "count_ratio"])
def test_cohort_group_metric_empty_preserves_metric_null_policy(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool, metric_name: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = ms.ref.dimension("sales.customer.region")
    categories = members.read(region)
    assert isinstance(categories, mv.LogicalCategoryRelation)
    eastern = categories.where(categories.value.eq("east")).members()
    metric = (
        mv.runtime_metric.ratio(
            ms.ref.metric("sales.order_count"),
            ms.ref.metric("sales.order_count"),
            label="count_ratio",
        )
        if metric_name == "count_ratio"
        else ms.ref.metric("sales." + metric_name)
    )
    via = ms.ref.relationship("sales.order_buyer")
    scope = mv.time_scope(start="2026-07-01", end="2026-08-01")
    first = members.group_by(region).observe(metric, during=scope, via=via).rollup()
    second = eastern.group_by(region).observe(metric, during=scope, via=via).rollup()
    design = mv.CohortContrast(pairing=mv.UnionKeys(missing="metric_empty"))
    for left, right in ((first, second), (first.execute(), second.execute())):
        result = left.compare(right, design=design).execute().to_pandas()
        if metric_name == "count_ratio":
            assert result["cell_tag"].tolist().count("defined") == 1
            assert result["cell_reason"].tolist().count("zero_denominator") == 2
            continue
        if metric_name == "order_count":
            assert result["cell_tag"].eq("defined").all()
            assert sorted(result["value"].tolist()) == [0, 1, 1]
            continue
        assert result["cell_tag"].tolist().count("defined") == 1
        assert result["cell_tag"].tolist().count("null") == 2
        assert (
            result.loc[result["cell_tag"] == "null", "cell_reason"].eq("empty_contribution").all()
        )


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize("event_kind", ["utc", "date", "aware_local"])
def test_period_change_retains_original_buckets_and_rejects_renumbering(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool, event_kind: str
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    if event_kind != "utc":
        model = case.root / "models/semantic/sales/models.py"
        model.write_text(
            model.read_text().replace(
                "parse=ms.timestamp(timezone='UTC')",
                "parse=None" if event_kind == "date" else "parse=ms.timestamp(timezone='UTC')",
            )
        )
        if event_kind == "date":
            with duckdb.connect(str(case.database_path)) as connection:
                connection.execute('ALTER TABLE "order" ALTER ordered_at TYPE DATE')
        ms.load(workspace_dir=case.root)
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.order_count"), ms.ref.relationship("sales.order_buyer")

    def observe(month: int, day: int, length: int) -> mv.LogicalNumericRelation:
        grid = mv.time_grid(
            during=mv.time_scope(
                start=f"2026-{month:02d}-{day:02d}"
                + ("T00:00:00-04:00" if event_kind == "aware_local" else ""),
                end=f"2026-{month:02d}-{day + length:02d}"
                + ("T00:00:00-04:00" if event_kind == "aware_local" else ""),
            ),
            grain=mv.grain("day"),
            timezone="America/New_York" if event_kind == "aware_local" else "UTC",
        )
        result = members.each(grid).observe(metric, during=grid.window, via=via)
        assert isinstance(result, mv.LogicalNumericRelation)
        return result

    current, baseline = observe(8, 14, 3), observe(7, 9, 3)
    design = mv.PeriodChange(alignment=mv.window_bucket())
    with pytest.raises(AnalysisError, match="equal-length"):
        current.compare(observe(7, 9, 2), design=design)
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        saved = left.compare(right, design=design).execute()
        frame = saved.to_pandas()
        assert len(frame) == 12
        assert sorted(frame["value"].tolist()) == [-1] + [0] * 11
        assert saved._dataset is not None
        mapping = next(
            part.table for part in saved._dataset.verified().parts if part.role == "correspondence"
        )
        assert (
            mapping["correspondence__current_key_1"].to_pylist()
            != mapping["correspondence__baseline_key_1"].to_pylist()
        )
        selected = left.where(left.value.gt(0))
        with pytest.raises(AnalysisError, match=r"period buckets|endpoint key|key_set_equal"):
            selected.compare(right, design=design).execute()


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    model = case.root / "models" / "semantic" / "sales" / "models.py"
    model.write_text(
        model.read_text()
        + """
account = ms.entity(name='account', datasource=warehouse, source=md.table('customer'), primary_key=['customer_id'])
account_id = ms.dimension_column(name='customer_id', entity=account, column='customer_id')
customer_account = ms.relationship(name='customer_account', from_entity=customer, to_entity=account, keys=[ms.join_on(customer_id, account_id)])
"""
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        model.write_text(
            model.read_text().replace(
                "source=md.table('customer')", "source=md.parquet('source_files/customer.parquet')"
            )
        )
    ms.load(workspace_dir=case.root)
    metric, buyer = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales.order_buyer")
    relationship = ms.ref.relationship("sales.customer_account")
    scope = mv.time_scope(start="2026-07-01", end="2026-08-01")
    left = case.session.members(ms.ref.entity("sales.customer")).observe(
        metric, during=scope, via=buyer
    )
    right = case.session.members(ms.ref.entity("sales.account")).observe(
        metric,
        during=scope,
        via=mv.routes(mv.route(ms.ref.entity("sales.order"), through=(buyer, relationship))),
    )
    for first, second in ((left, right), (left.execute(), right.execute())):
        pairing = mv.one_to_one(left=first, right=second, via=relationship)
        result = first.ratio(second, pairing=pairing).execute().to_pandas()
        assert result["value"].dropna().tolist() == [1.0, 1.0, 1.0]
        with pytest.raises(AnalysisError, match="one-to-one"):
            mv.one_to_one(left=first, right=second, via=buyer)
        with pytest.raises(AnalysisError, match="reused"):
            second.ratio(first, pairing=pairing)


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_relative_and_ratio_cold_fixed_composition(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    current = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    baseline = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    first, second = current.execute(), baseline.execute()
    relative = current.rollup().compare(baseline.rollup(), value="relative_change").execute()
    positive = current.where(current.value.gt(0))
    ratio = positive.ratio(positive).execute()
    refs = [item.state.artifact_ref.ref for item in (first, second, relative, ratio)]
    source = case.root / "source_files" if parquet else case.database_path
    offline = source.with_name(source.name + ".offline")
    source.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("fixed comparison reopened a source or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
session = mv.session.resume(sys.argv[1], by="id")
first, second, relative, ratio = [session.artifact(ref) for ref in sys.argv[2:]]
assert isinstance(relative, mv.MaterializedDifferenceRelation)
assert isinstance(ratio, mv.MaterializedNumericRelation)
for saved in (relative, ratio):
    assert saved._dataset.artifact.descriptor.method_state.contract_version == 1
    selected = saved.where(saved.value.gt(0)).execute()
    assert selected._dataset.artifact.descriptor.method_state.contract_version == 1
    assert "rollup" not in {action.call.split("(")[0].removeprefix("relation.") for action in saved.contract().actions}
result = first.compare(second, value="relative_change").execute().to_pandas().set_index("member")
assert result.loc["A", "value"] == -0.4
assert result.loc["B", "value"] == 0.2
assert result.loc["D", "cell_reason"] == "zero_baseline"
result = first.ratio(first).execute().to_pandas().set_index("member")
assert result.loc["A", "value"] == 1.0
assert result.loc["D", "cell_reason"] == "zero_denominator"
"""
    try:
        process = subprocess.run(
            [sys.executable, "-c", script, case.session.id, *refs],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        offline.rename(source)
    assert process.returncode == 0, process.stderr


@pytest.mark.runtime
@pytest.mark.parametrize("physical,parquet", _NUMERIC_SOURCES)
def test_ordinary_ratio_numeric_families(
    analysis_dsl_case_factory: DslCaseFactory, physical: str, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, physical, parquet)
    members = case.session.members(ms.ref.entity("sales.customer"))
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed = observed.execute()
    for result in (observed.ratio(observed).execute(), fixed.ratio(fixed).execute()):
        frame = result.to_pandas().set_index("member")
        assert frame.loc["A", "value"] == 1
        assert frame.loc["B", "value"] == 1
        assert frame.loc["D", "cell_reason"] == "zero_denominator"
        assert result._dataset is not None
        assert result._dataset.artifact.descriptor.method_state.contract_version == 1


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_composite_keys_double_empty_and_wrong_key_images(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("ALTER TABLE customer ADD tenant BIGINT DEFAULT 7")
        connection.execute('ALTER TABLE "order" ADD tenant BIGINT DEFAULT 7')
    model = case.root / "models/semantic/sales/models.py"
    text = model.read_text().replace(
        "primary_key=['customer_id']", "primary_key=['tenant', 'customer_id']"
    )
    text = text.replace(
        "buyer = ms.relationship",
        "customer_tenant = ms.dimension_column(name='tenant', entity=customer, column='tenant')\norder_tenant = ms.dimension_column(name='tenant', entity=orders, column='tenant')\nbuyer = ms.relationship",
    )
    text = text.replace(
        "keys=[ms.join_on(order_customer_id, customer_id)]",
        "keys=[ms.join_on(order_tenant, customer_tenant), ms.join_on(order_customer_id, customer_id)]",
    )
    model.write_text(text)
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    current = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    baseline = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    first, second = current.execute(), baseline.execute()
    for left, right in ((current, baseline), (first, second)):
        result = left.compare(right).execute()
        frame = result.to_pandas()
        assert frame["value"].tolist() == [-40, 20, -50, 0]
        assert result._dataset is not None
        assert len(result._dataset.verified().contract.key_fields) == 2
        empty_left, empty_right = (
            left.where(left.value.gt(10000)),
            right.where(right.value.gt(10000)),
        )
        assert empty_left.compare(empty_right).execute().to_pandas().empty
        assert (
            empty_left.compare(
                empty_right, design=mv.TimeChange(pairing=mv.UnionKeys(missing="keep"))
            )
            .execute()
            .to_pandas()
            .empty
        )
        # Each side has one row, but A and D are different complete identities.
        wrong_left, wrong_right = left.where(left.value.eq(60)), right.where(right.value.eq(50))
        with pytest.raises(AnalysisError):
            wrong_left.compare(wrong_right).execute()


@pytest.mark.runtime
@pytest.mark.parametrize("baseline_amount", [-10, -(2**63)])
def test_public_relative_negative_and_minimum_integer_baseline(
    analysis_dsl_case_factory: DslCaseFactory, baseline_amount: int
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            'INSERT INTO "order" (order_id, customer_id, channel, status, ordered_at, amount) VALUES (?, ?, ?, ?, ?, ?)',
            ["min-old", "A", "web", "paid", "2026-07-10", baseline_amount],
        )
        connection.execute(
            'INSERT INTO "order" (order_id, customer_id, channel, status, ordered_at, amount) VALUES (?, ?, ?, ?, ?, ?)',
            ["min-new", "A", "web", "paid", "2026-08-10", 2**63 - 1],
        )
    members = case.session.members(ms.ref.entity("sales.customer"))
    ids = members.read(ms.ref.dimension("sales.customer.customer_id"))
    members = ids.where(ids.value.eq("A")).members()
    current = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    baseline = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    first, second = current.execute(), baseline.execute()
    expected = float(Fraction(2**63 - 1 - baseline_amount, abs(baseline_amount)))
    for left, right in ((current, baseline), (first, second)):
        assert (
            left.compare(right, value="relative_change").execute().to_pandas().iloc[0]["value"]
            == expected
        )
        with pytest.raises(AnalysisError):
            left.compare(right).execute()


@pytest.mark.runtime
def test_float_denominator_interval_and_mean_operand_envelope(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" (order_id, customer_id, channel, status, ordered_at, amount) VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("old-1", "A", "web", "paid", "2026-07-10", 1.0),
                ("old-2", "A", "web", "paid", "2026-07-10", -1.0 + 1e-13),
                ("new", "A", "web", "paid", "2026-08-10", 2.0),
            ],
        )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\naverage = ms.aggregate(name='average', measure=amount, agg='mean', time=ordered_at)\n"
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    ids = members.read(ms.ref.dimension("sales.customer.customer_id"))
    members = ids.where(ids.value.eq("A")).members()
    for metric in ("revenue", "average"):
        current = members.observe(
            ms.ref.metric("sales." + metric),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        baseline = members.observe(
            ms.ref.metric("sales." + metric),
            during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        first, second = current.execute(), baseline.execute()
        for left, right in ((current, baseline), (first, second)):
            result = left.compare(right).execute()
            assert result.to_pandas().iloc[0]["cell_tag"] == "defined"
            assert result._dataset is not None
            mapping = next(
                part.table
                for part in result._dataset.verified().parts
                if part.role == "correspondence"
            )
            assert mapping["correspondence__baseline_error_bound"][0].as_py() > 1e-12
            with pytest.raises(AnalysisError):
                left.compare(right, value="relative_change").execute()


@pytest.mark.runtime
def test_comparison_construction_is_lazy_and_shared_source_runs_once(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis.expr.types as ir

    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import CompiledRead, SourceSession

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    independent = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    original = SourceSession.stage_derived
    captured: list[pa.Table] = []

    def stage(session: SourceSession, read: CompiledRead) -> tuple[ir.Table, pa.Table]:
        relation, table = original(session, read)
        if "original_state__sum" in read.schema.names:
            captured.append(table)
        return relation, table

    monkeypatch.setattr(SourceSession, "stage_derived", stage)
    ratio = observed.ratio(observed)
    with pytest.raises(AnalysisError, match="same frozen member node"):
        observed.compare(independent)
    assert not captured
    first = ratio.execute()
    assert len(captured) == 1
    second = ratio.execute()
    assert len(captured) == 2
    assert first.state.artifact_ref != second.state.artifact_ref
    assert first.to_pandas().equals(second.to_pandas())


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["ratio", "linear", "weighted"])
def test_comparison_of_float_original_expression_operands(
    analysis_dsl_case_factory: DslCaseFactory, kind: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    ids = members.read(ms.ref.dimension("sales.customer.customer_id"))
    members = ids.where(ids.value.eq("A")).members()
    base = ms.ref.metric("sales.revenue")
    amount = ms.ref.measure("sales.order.amount")
    metric = (
        mv.runtime_metric.ratio(base, base, label="ratio")
        if kind == "ratio"
        else mv.runtime_metric.linear(add=[base, base], label="linear")
        if kind == "linear"
        else mv.runtime_metric.weighted_mean(amount, amount, label="weighted")
    )
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    first, second = current.execute(), baseline.execute()
    expected = {"ratio": 0.0, "linear": -80.0, "weighted": -40.0}[kind]
    for left, right in ((current, baseline), (first, second)):
        result = left.compare(right).execute()
        assert result.to_pandas()["value"].tolist() == [expected]
        assert result._dataset is not None
        mapping = next(
            part.table for part in result._dataset.verified().parts if part.role == "correspondence"
        )
        assert mapping["correspondence__current_error_bound"][0].as_py() > 0


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["null", "undefined"])
def test_union_keeps_present_nondefined_cell_separate_from_absence(
    analysis_dsl_case_factory: DslCaseFactory, kind: str
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j1")
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = ms.ref.dimension("sales.customer.region")
    categories = members.read(region)
    eastern = categories.where(categories.value.eq("east")).members()
    count = ms.ref.metric("sales.order_count")
    metric = (
        ms.ref.metric("sales.revenue")
        if kind == "null"
        else mv.runtime_metric.ratio(count, count, label="count_ratio")
    )
    scope = mv.time_scope(start="2026-08-01", end="2026-09-01")
    via = ms.ref.relationship("sales.order_buyer")
    current = eastern.group_by(region).observe(metric, during=scope, via=via).rollup()
    baseline = members.group_by(region).observe(metric, during=scope, via=via).rollup()
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        saved = left.compare(
            right, design=mv.CohortContrast(pairing=mv.UnionKeys(missing="keep"))
        ).execute()
        frame = saved.to_pandas()
        assert frame["cell_reason"].tolist().count("missing_side") == 2
        assert saved._dataset is not None
        checked = saved._dataset.verified()
        endpoint = next(part.table for part in checked.parts if part.role == "baseline_endpoint")
        assert kind in endpoint["baseline_endpoint__cell_tag"].to_pylist()
        with pytest.raises(AnalysisError):
            left.compare(
                right, design=mv.CohortContrast(pairing=mv.UnionKeys(missing="metric_empty"))
            ).execute()


@pytest.mark.runtime
def test_public_decimal_ratio_rounds_once_half_even(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(30,6)')
        connection.execute('ALTER TABLE "order" ADD denominator DECIMAL(30,6) DEFAULT 2')
        connection.execute(
            "UPDATE \"order\" SET amount = CASE WHEN customer_id = 'A' THEN 0.000001 ELSE 0.000003 END"
        )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\ndenominator_value = ms.measure_column(name='denominator', entity=orders, column='denominator', additivity=ms.additive_all(), unit='CNY')\ndenominator_metric = ms.aggregate(name='denominator', measure=denominator_value, agg='sum', time=ordered_at)\n"
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    scope = mv.time_scope(start="2026-08-01", end="2026-09-01")
    via = ms.ref.relationship("sales.order_buyer")
    numerator = members.observe(ms.ref.metric("sales.revenue"), during=scope, via=via)
    denominator = members.observe(ms.ref.metric("sales.denominator"), during=scope, via=via)
    first, second = numerator.execute(), denominator.execute()
    for left, right in ((numerator, denominator), (first, second)):
        values = left.ratio(right).execute().to_pandas().set_index("member")["value"].to_dict()
        assert values == {
            "A": Decimal("0.000000"),
            "B": Decimal("0.000002"),
            "C": Decimal("0.000002"),
            "D": Decimal("0.000002"),
        }


@pytest.mark.runtime
def test_ordinary_ratio_over_untimed_numeric_read(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.measure("sales.order.amount")
    )
    fixed = values.execute()
    for operand in (values, fixed):
        result = operand.ratio(operand).execute().to_pandas()
        defined = result.loc[result["cell_tag"] == "defined", "value"]
        assert len(defined) > 0 and defined.eq(1).all()
        assert (
            result.loc[result["cell_tag"] == "undefined", "cell_reason"]
            .eq("zero_denominator")
            .all()
        )


@pytest.mark.runtime
def test_comparison_rejects_duplicate_target_keys(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("INSERT INTO customer VALUES ('A', 'east')")
    members = case.session.members(ms.ref.entity("sales.customer"))
    current = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    baseline = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    with pytest.raises(AnalysisError):
        current.compare(baseline).execute()


@pytest.mark.runtime
def test_float_fold_comparison_without_error_envelope_rejects_statically(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER COLUMN amount TYPE DOUBLE')
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text()
        + "\nstatus_amount = ms.measure_column(name='status_amount', entity=orders, "
        "column='amount', additivity=ms.additive_all(except_=(ordered_at,)), "
        "status_time_dimension=ordered_at, status_time_fold='mean', unit='CNY')\n"
        "folded = ms.aggregate(name='folded', measure=status_amount, agg='sum')\n"
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    observed = members.observe(
        ms.ref.metric("sales.folded"),
        during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    with pytest.raises(AnalysisError, match="retained operand error envelope"):
        observed.ratio(observed)
    actions = {
        action.call.split("(")[0].removeprefix("relation.")
        for action in observed.contract().actions
    }
    assert "compare" not in actions and "ratio" not in actions


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["sum", "mean"])
def test_float_row_statistic_comparison_retains_error_state(
    analysis_dsl_case_factory: DslCaseFactory,
    method: str,
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                (f"order-{i}", customer, "web", "paid", "2026-08-10", amount)
                for i, (customer, amount) in enumerate(
                    zip("ABCD", [1e16, -1e16, 1.0, 1.0], strict=True)
                )
            ],
        )
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    descriptor = mv.sum() if method == "sum" else mv.mean()
    grouped = observed.group_by(ms.ref.entity("sales.customer")).summarize(descriptor)
    for statistic in (observed.summarize(descriptor), grouped.rollup(), grouped.execute().rollup()):
        fixed = statistic.execute()
        assert fixed._dataset is not None
        state = next(
            part.table for part in fixed._dataset.verified().parts if part.role == "row_state"
        )
        assert state["row_state__error_bound"][0].as_py() >= 20000.0
        for operand in (statistic, fixed):
            with pytest.raises(AnalysisError, match="denominator interval"):
                operand.ratio(operand).execute()


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["sum", "mean", "min", "max"])
def test_stable_float_row_statistic_comparison_propagates_bounds(
    analysis_dsl_case_factory: DslCaseFactory,
    method: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "DOUBLE", True)
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    # Ordinary division produces float Cells with retained nonzero bounds.
    assert isinstance(observed, mv.LogicalNumericRelation)
    positive = observed.where(observed.value.gt(0))
    floating = positive.ratio(positive)
    descriptor = {"sum": mv.sum(), "mean": mv.mean(), "min": mv.min(), "max": mv.max()}[method]
    statistic = floating.summarize(descriptor)
    for operand in (statistic, statistic.execute()):
        result = operand.ratio(operand).execute()
        assert result.to_pandas()["value"].tolist() == [1.0]
        assert result._dataset is not None
        correspondence = next(
            part.table for part in result._dataset.verified().parts if part.role == "correspondence"
        )
        assert correspondence["correspondence__baseline_error_bound"][0].as_py() > 0


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_period_change_after_bucket_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))

    def grouped(month: int, day: int) -> mv.LogicalRolledNumericRelation:
        grid = mv.time_grid(
            during=mv.time_scope(
                start=f"2026-{month:02d}-{day:02d}", end=f"2026-{month:02d}-{day + 3:02d}"
            ),
            grain=mv.grain("day"),
            timezone="UTC",
        )
        observed = members.each(grid).observe(
            ms.ref.metric("sales.order_count"),
            during=grid.window,
            via=ms.ref.relationship("sales.order_buyer"),
        )
        rolled = observed.group_by(grid).rollup()
        assert isinstance(rolled, mv.LogicalRolledNumericRelation)
        return rolled

    current, baseline = grouped(8, 14), grouped(7, 9)
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        saved = left.compare(right, design=mv.PeriodChange(alignment=mv.window_bucket())).execute()
        assert sorted(saved.to_pandas()["value"].tolist()) == [-1, 0, 0]


@pytest.mark.runtime
def test_fixed_comparison_indexes_components_once_per_endpoint(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_local_execution as local
    from marivo.analysis.materialization.graph_exchange import ExchangeResult

    case = analysis_dsl_case_factory("j2")
    _numeric_source(case, "DOUBLE", True)
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    positive = observed.where(observed.value.gt(0))
    fixed = positive.ratio(positive).execute()
    calls = []
    original = local._operand_components

    def indexed(source: ExchangeResult) -> dict[tuple[object, ...], dict[str, object]]:
        calls.append(source.primary.num_rows)
        return original(source)

    monkeypatch.setattr(local, "_operand_components", indexed)
    result = fixed.ratio(fixed).execute()
    assert result.to_pandas()["value"].eq(1.0).all()
    assert len(calls) == 2
    assert all(count > 1 for count in calls)
