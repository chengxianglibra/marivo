"""Strict selection with independent row and consumption expectations."""

import json
from decimal import Decimal

import pytest
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import Binding
from marivo.analysis.core.predicates import TemporalLiteral, ValuePredicate, compose
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.graph_protocol import decode, encode
from marivo.analysis.methods.physical import DecimalType, DurationType
from marivo.analysis.methods.predicates import evaluate_leaf, validate_operand
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.parametrize(
    "value",
    [
        "123",
        "1.20",
        "1e3",
        "west",
        1,
        1.0,
        True,
        Decimal("1.20"),
        Decimal("-0.00"),
        TemporalLiteral("date", "2026-08-01"),
        TemporalLiteral("timestamp", "2026-08-01T00:00:00+00:00"),
    ],
)
def test_predicate_literal_wire_preserves_exact_type(
    value: int | float | Decimal | str | bool | TemporalLiteral,
) -> None:
    adapter = TypeAdapter(ValuePredicate)
    predicate = ValuePredicate(Binding("s", "o", "input", "scope"), "eq", value)
    restored = decode(encode(predicate, adapter), adapter)
    assert type(restored.value) is type(value)
    assert restored.value == value
    if isinstance(value, Decimal):
        assert isinstance(restored.value, Decimal)
        assert restored.value.as_tuple() == value.as_tuple()


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "decimal", "value": "invalid"},
        {"kind": "decimal", "value": 123},
        {"kind": "decimal", "value": "1.20", "extra": True},
        {"kind": "unknown", "value": "1.20"},
    ],
)
def test_malformed_decimal_literal_wire_rejects(value: dict[str, object]) -> None:
    adapter = TypeAdapter(ValuePredicate)
    predicate = ValuePredicate(Binding("s", "o", "input", "scope"), "eq", Decimal("1.20"))
    payload: dict[str, object] = json.loads(encode(predicate, adapter))
    payload["value"] = value
    with pytest.raises(IntegrityError, match="invalid closed metadata"):
        decode(canonical_json(payload), adapter)


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_numeric_string_category_persists_and_continues(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            f'UPDATE "{case.names.customer}" SET region = ? WHERE "{case.names.customer_id}" = ?',
            ["123", "A"],
        )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    for current in (category, category.execute()):
        selected = current.where(current.value.eq("123"))
        assert type(selected) is mv.LogicalCategoryRelation
        chained = selected.where(selected.value.eq("123"))
        assert type(chained) is mv.LogicalCategoryRelation
        saved = chained.execute()
        assert type(saved) is mv.MaterializedCategoryRelation
        frame = saved.to_pandas()
        assert frame.member.tolist() == ["A"]
        assert frame.value.tolist() == ["123"]
        restored = case.session.artifact(saved.state.artifact_ref)
        assert isinstance(restored, mv.MaterializedCategoryRelation)
        assert restored.to_pandas().equals(frame)
        continued = restored.where(restored.value.eq("123")).execute()
        assert continued.to_pandas().equals(frame)


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_selected_difference_discloses_where_continuation(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    from marivo._help.model import NativeHelpRoute
    from marivo._help.route import route_help_target

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    current, baseline = (
        members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start=start, end=end),
            via=ms.ref.relationship(f"sales.{case.names.buyer}"),
            by=(mv.member(),),
        )
        for start, end in (("2026-08-01", "2026-09-01"), ("2026-07-01", "2026-08-01"))
    )
    difference = current.compare(baseline)
    selected = difference.where(difference.value.is_defined())
    for relation in (selected, selected.execute()):
        action = next(
            action
            for action in relation.contract().actions
            if action.call == "relation.where(predicate)"
        )
        assert isinstance(route_help_target(action.help_target), NativeHelpRoute)
        result = relation.where(relation.value.is_defined()).execute()
        assert len(result.to_pandas()) == len(analysis_dsl_rows("j2").customers)


def test_strict_leaf_and_three_value_composition() -> None:
    binding = Binding("s", "o", "input", "scope")
    comparison = ValuePredicate(binding, "gt", 0)
    state = ValuePredicate(binding, "is_defined", 0)
    undefined = {"value": None, "cell_tag": "undefined"}
    assert evaluate_leaf(state, undefined, None) is False
    with pytest.raises(Exception, match="Cell tag undefined"):
        evaluate_leaf(comparison, undefined, None)
    assert (
        evaluate_leaf(comparison, {"value": None, "cell_tag": "unknown"}, None, cohort=True) is None
    )
    tree = ValuePredicate(binding, "all_of", 0, children=(state, comparison))
    assert compose(tree, (False, None)) is False
    assert (
        compose(ValuePredicate(binding, "any_of", 0, children=(state, comparison)), (True, None))
        is True
    )


def test_precise_predicate_literals_and_duration_fields() -> None:
    binding = Binding("s", "o", "input", "scope")
    validate_operand(ValuePredicate(binding, "gt", Decimal("1.20")), DecimalType(18, 2), None)
    with pytest.raises(Exception):
        validate_operand(ValuePredicate(binding, "gt", Decimal("1.201")), DecimalType(18, 2), None)
    with pytest.raises(Exception):
        validate_operand(ValuePredicate(binding, "gt", 1), DurationType("us"), None)
    validate_operand(
        ValuePredicate(binding, "gt", 0, right_index=1), DurationType("ns"), DurationType("ns")
    )


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_public_multi_input_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    amount = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(mv.member(),),
    )
    twice = members.observe(
        mv.runtime_metric.linear(
            add=[ms.ref.metric("sales.revenue"), ms.ref.metric("sales.revenue")], label="twice"
        ),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(mv.member(),),
    )
    predicate = mv.all_of(amount.value.is_defined(), amount.value.gt(0), category.value.eq("east"))
    facts = analysis_dsl_rows("j2")
    east = {member for member, region in facts.customers if region == "east"}
    totals: dict[str, float] = {}
    for _, member, _, _, occurred, value in facts.orders:
        if member is not None and str(occurred).startswith("2026-08"):
            totals[member] = totals.get(member, 0) + value
    expected = {member for member, total in totals.items() if total > 0 and member in east}
    assert set(amount.where(predicate).execute().to_pandas().member) == expected
    positive = {member for member, total in totals.items() if total > 0}
    assert set(amount.where(amount.value.lt(twice.value)).execute().to_pandas().member) == positive
    fixed_amount, fixed_category = amount.execute(), category.execute()
    fixed_twice = twice.execute()
    assert (
        set(
            fixed_amount.where(fixed_amount.value.lt(fixed_twice.value))
            .execute()
            .to_pandas()
            .member
        )
        == positive
    )
    selected = fixed_amount.where(
        mv.all_of(fixed_amount.value.gt(0), fixed_category.value.eq("east"))
    ).execute()
    assert set(selected.to_pandas().member) == expected
    with pytest.raises(Exception):
        bool(predicate)


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_tag_selection_precedes_numeric_consumption(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    n = case.names
    model = case.root / "models" / "semantic" / n.domain / "models.py"
    model.write_text(
        model.read_text().replace(
            "agg='sum', time=ordered_at)", "agg='sum', time=ordered_at, empty=ms.empty.null())", 1
        )
    )
    import duckdb

    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'DELETE FROM "{n.order}" WHERE "{n.customer_id}" = ?', ["D"])
    ms.load(workspace_dir=case.root)
    targets = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    values = targets.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    current = values.execute() if fixed else values
    with pytest.raises(Exception, match=r"Defined|defined"):
        current.where(mv.all_of(current.value.is_defined(), current.value.gt(0))).execute()
    with pytest.raises(Exception, match=r"Defined|defined"):
        current.where(mv.any_of(current.value.is_defined(), current.value.gt(0))).execute()
    selected = current.where(current.value.is_defined())
    category = targets.read(ms.ref.dimension(f"{n.domain}.{n.customer}.region"))
    region = category.execute() if fixed else category
    east = selected.where(mv.all_of(selected.value.gt(0), region.value.eq("east"))).execute()
    assert set(east.to_pandas().member) == {
        member for member, name in analysis_dsl_rows("j2").customers if name == "east"
    }
    if fixed:
        import os
        import subprocess
        import sys

        offline = case.database_path.with_suffix(".offline")
        case.database_path.rename(offline)
        script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("fixed selection cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
session = mv.session.resume(sys.argv[1], by="id")
values, region, saved = [session.artifact(ref) for ref in sys.argv[2:]]
selected = values.where(values.value.is_defined())
result = selected.where(mv.all_of(selected.value.gt(0), region.value.eq("east"))).execute()
assert result.to_pandas().equals(saved.to_pandas())
assert result.members(through=result.subject_binding).execute().to_pandas().member.tolist() == saved.to_pandas().member.tolist()
"""
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    case.session.id,
                    current.state.artifact_ref.ref,
                    region.state.artifact_ref.ref,
                    east.state.artifact_ref.ref,
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
    positive = selected.where(selected.value.gt(0)).execute()
    assert positive.to_pandas().cell_tag.eq("defined").all()
    ancestor = selected.where(current.value.gt(0)).execute()
    assert ancestor.to_pandas().equals(positive.to_pandas())
    projected = positive.members(through=positive.subject_binding).execute()
    assert set(projected.to_pandas().member) == set(positive.to_pandas().member)


@pytest.mark.runtime
@pytest.mark.parametrize("physical", ["BIGINT", "DOUBLE", "DECIMAL(18,2)"])
@pytest.mark.parametrize("parquet", [False, True])
def test_public_precise_field_comparisons(
    analysis_dsl_case_factory: DslCaseFactory, physical: str, parquet: bool
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    n = case.names
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'ALTER TABLE "{n.order}" ALTER "{n.amount}" TYPE {physical}')
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    values = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}")).observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    values = values.where(values.value.is_defined())
    facts = analysis_dsl_rows("j2")
    totals: dict[str, float] = {}
    for _, member, _, _, occurred, amount in facts.orders:
        if member is not None and str(occurred).startswith("2026-08"):
            totals[member] = totals.get(member, 0) + amount
    for current in (values, values.execute()):
        matched = current.where(current.value.eq(current.value)).execute()
        assert set(matched.to_pandas().member) == set(totals)
        threshold = Decimal("0.00") if physical.startswith("DECIMAL") else 0
        saved = current.where(current.value.gt(threshold)).execute()
        frame = saved.to_pandas()
        assert set(frame.member) == {member for member, total in totals.items() if total > 0}
        if isinstance(threshold, Decimal):
            restored = case.session.artifact(saved.state.artifact_ref)
            assert isinstance(restored, mv.MaterializedSelectedNumericRelation)
            assert restored.where(restored.value.gt(threshold)).execute().to_pandas().equals(frame)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "unit,parquet", [("us", False), ("s", True), ("ms", True), ("us", True), ("ns", True)]
)
def test_public_duration_field_comparison(
    analysis_dsl_case_factory: DslCaseFactory, unit: str, parquet: bool
) -> None:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        path = case.root / "source_files/order.parquet"
        table = pq.read_table(path)
        pq.write_table(
            table.set_column(
                table.schema.get_field_index("amount"),
                "amount",
                table["amount"].cast(pa.duration(unit)),
            ),
            path,
        )
    else:
        with duckdb.connect(str(case.database_path)) as db:
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
    ms.load(workspace_dir=case.root)
    values = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(mv.member(),),
    )
    selected = values.where(values.value.is_defined())
    expected = {
        member
        for _, member, _, _, occurred, _ in analysis_dsl_rows("j2").orders
        if member is not None and str(occurred).startswith("2026-08")
    }
    for current in (selected, selected.execute()):
        result = current.where(current.value.eq(current.value)).execute()
        assert set(result.to_pandas().member) == expected
        with pytest.raises(Exception, match="incompatible"):
            current.where(current.value.gt(0))


@pytest.mark.runtime
def test_foreign_predicate_inputs_reject_before_execution(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = case.session.members(ms.ref.entity("sales.customer")).read(
        ms.ref.dimension("sales.customer.region")
    )
    fixed = values.execute()
    other = (
        mv.session.get_or_create("foreign-r63", report_timezone="UTC")
        .members(ms.ref.entity("sales.customer"))
        .read(ms.ref.dimension("sales.customer.region"))
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("construction cannot start a Run")

    monkeypatch.setattr(type(values._runtime), "_execute_graph", forbidden)
    with pytest.raises(Exception, match="Session"):
        values.where(other.value.eq("east"))
    with pytest.raises(Exception, match=r"mixed|source.*fixed"):
        values.where(fixed.value.eq("east"))
    with pytest.raises(Exception, match=r"foreign|mapping|Subject"):
        fixed.members(through=other.subject_binding)


def test_closed_policy_and_predicate_inputs() -> None:
    from marivo.analysis.errors import AnalysisError

    for value in (True, 0, -1, 2**63):
        with pytest.raises(AnalysisError):
            mv.at_least(value)
    with pytest.raises(TypeError):
        mv.all_instances()
    with pytest.raises(AnalysisError):
        mv.all_instances(empty=ms.empty.zero())
    with pytest.raises(AnalysisError):
        mv.SubjectBinding()
    with pytest.raises(AnalysisError):
        mv.all_of(True, False)
    binding = Binding("s", "o", "input", "scope")
    with pytest.raises(AnalysisError):
        validate_operand(
            ValuePredicate(binding, "gt", 0, right_index=1), DurationType("us"), DurationType("ns")
        )
