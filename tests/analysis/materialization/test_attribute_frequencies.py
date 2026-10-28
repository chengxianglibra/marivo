"""Exact quantile capture reaches the existing governed SourceRead boundary."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import ibis
import ibis.expr.types as ir
import pandas as pd
import pytest

from marivo._definition.expressions import RowField, SourceAggregate
from marivo._definition.types import ExactRef, SumAll
from marivo.analysis.compiler.attribute_frequencies import frequency_read, share_frequency_reads
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import (
    AttributeFrequencyRead,
    QueryPlan,
    ReductionRead,
    SourceRead,
)
from marivo.analysis.compiler.observation import ReductionInput
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.value_types import data_type
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    qualify_attribute_game_sources,
    read_stage,
)
from marivo.refs import ref
from tests.datasource.source_cases import SourceData, source_case, source_params


def _stage(
    table: ir.Table, backend: str, *, strict: bool = False, distinct: bool = False, q: float = 0.5
) -> SourceRead:
    operand = RowField(
        ExactRef(ref.measure("sales.facts.amount"), "v1"),
        ExactRef(ref.entity("sales.facts"), "v1"),
        None,
        SumAll(),
    )
    reduction = ReductionInput(
        SourceAggregate(
            operand,
            "count_distinct" if distinct else "quantile",
            None,
            q=None if distinct else q,
            nulls="error" if strict else "ignore",
        ),
        (("key.0", "sales.facts.category"),),
        "value",
        provider=backend,
    )
    return SourceRead(
        "read:0",
        "facts",
        (0,),
        (
            DataColumn("key.0", "category", data_type(table.category.type()), True),
            DataColumn(
                "value",
                "value",
                data_type(
                    table.amount.nunique().type() if distinct else table.amount.quantile(q).type()
                ),
                True,
            ),
        ),
        ("key.0",),
        ReductionRead(reduction),
        (("sales.facts.amount", "amount"), ("sales.facts.category", "category")),
        (),
        None,
    )


@pytest.mark.parametrize("backend", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
def test_frequency_sql_groups_typed_values_without_rank_or_coalition_expansion(
    backend: str,
) -> None:
    table = ibis.table({"category": "string", "amount": "decimal(18,2)"}, name="facts")
    stage = frequency_read(_stage(table, backend, distinct=True))
    plan = QueryPlan((stage,), ((stage.stage_id, ()),), stage.stage_id)
    lowered = lower_query(plan, {"facts": table})
    sql = ibis.to_sql(lowered.read(stage.stage_id).expression, dialect=backend).lower()
    assert "group by" in sql and "__attribute_frequency_value" in sql
    assert "__attribute_frequency" in sql and "count(*)" in sql
    assert "row_number" not in sql and "percentile" not in sql and "quantile(" not in sql
    assert 'cast("amount" as double' not in sql


DATA = SourceData(
    "id BIGINT, category VARCHAR(10), amount DECIMAL(18,2)",
    "(1,'A',5.00),(2,'A',5.00),(3,'A',NULL),(4,'B',15.00),(5,'B',25.00),(6,NULL,40.00)",
    "id Int64, category Nullable(String), amount Nullable(Decimal(18,2))",
    [
        {"id": index, "category": category, "amount": None if amount is None else Decimal(amount)}
        for index, (category, amount) in enumerate(
            [
                ("A", "5.00"),
                ("A", "5.00"),
                ("A", None),
                ("B", "15.00"),
                ("B", "25.00"),
                (None, "40.00"),
            ],
            1,
        )
    ],
)


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
@pytest.mark.parametrize("distinct", [False, True], ids=["quantile", "distinct"])
def test_native_frequency_read_preserves_groups_counts_and_decimal_carrier(
    backend: str, profile: str, distinct: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = (
        replace(
            DATA,
            columns=DATA.columns.replace("VARCHAR(10)", "VARCHAR(10) COLLATE utf8mb4_0900_bin"),
        )
        if backend == "mysql"
        else DATA
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="attribute-frequency")
        stage = frequency_read(_stage(bound.relation, backend, distinct=distinct))
        plan = QueryPlan((stage,), ((stage.stage_id, ()),), stage.stage_id)
        if not distinct:
            with pytest.raises(AnalysisStageUnavailableError):
                lower_query(plan, {"facts": bound.relation})
            assert case.session.submissions == []
            return
        lowered = lower_query(plan, {"facts": bound.relation})
        prepared = PreparedSources(case.session, (), {"facts": bound.relation}, {"facts": bound})
        qualify_attribute_game_sources(prepared, plan)
        output = read_stage(
            prepared, lowered.plan.source_read(stage.stage_id), lowered.read(stage.stage_id)
        )
        (capture,) = output.attribute_frequencies
        states = dict(capture.groups)
        assert set(states) == {("A",), ("B",), (None,)}
        assert states[("A",)].values == (Decimal("5.00"),)
        assert states[("A",)].frequencies == (2,)
        assert states[("A",)].support_count == 3 and states[("A",)].null_count == 1
        assert states[("B",)].values == (Decimal("15.00"), Decimal("25.00"))
        assert all(
            isinstance(value, Decimal) for state in states.values() for value in state.values
        )
        assert output.source_rows == 5
        assert {
            None if pd.isna(key) else key: value
            for key, value in output.table.itertuples(index=False, name=None)
        } == (
            {"A": 1, "B": 2, None: 1}
            if distinct
            else {"A": Decimal("5.00"), "B": Decimal("20.00"), None: Decimal("40.00")}
        )
        assert capture.state_entries == 5 and capture.returned_rows == 5
        assert capture.decoded_arrow_bytes > 0
        (costs,) = output.attribute_state_captures
        assert costs.stage_id == capture.stage_id and costs.provider == backend
        assert costs.returned_rows == capture.returned_rows
        assert costs.decoded_arrow_bytes == capture.decoded_arrow_bytes
        assert costs.state_route == ("typed_membership" if distinct else "typed_frequencies")
        assert costs.state_entries == capture.state_entries
        assert len(case.session.submissions) == 1


def test_frequency_capture_does_not_admit_approximate_quantiles() -> None:
    table = ibis.table({"category": "string", "amount": "float64"}, name="facts")
    stage = _stage(table, "duckdb")
    assert isinstance(stage.program, ReductionRead)
    node = stage.program.reduction.node
    assert isinstance(node, SourceAggregate)
    stage = replace(
        stage,
        program=ReductionRead(
            replace(stage.program.reduction, node=replace(node, reducer="approx_quantile"))
        ),
    )
    with pytest.raises(
        AnalysisStageUnavailableError, match="independent exact numeric source reduction"
    ):
        frequency_read(stage)


@pytest.mark.parametrize("dtype", ["string", "float64"])
def test_distinct_frequency_rejects_unqualified_equality_before_reads(dtype: str) -> None:
    table = ibis.table({"category": "string", "amount": dtype}, name="facts")
    original = _stage(table, "duckdb", distinct=True)
    assert isinstance(original.program, ReductionRead)
    node = original.program.reduction.node
    assert isinstance(node, SourceAggregate) and isinstance(node.operand, RowField)
    original = replace(
        original,
        program=ReductionRead(
            replace(
                original.program.reduction,
                node=replace(
                    node,
                    operand=replace(
                        node.operand, ref=ExactRef(ref.dimension("sales.facts.amount"), "v1")
                    ),
                ),
            )
        ),
    )
    stage = frequency_read(original)
    with pytest.raises(AnalysisStageUnavailableError, match="integer/Decimal distinct membership"):
        lower_query(QueryPlan((stage,), (), stage.stage_id), {"facts": table})


def test_native_frequency_capture_keeps_full_uint64_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.data_types import DecimalDataType

    maximum = 2**64 - 1
    data = SourceData(
        "id BIGINT, category VARCHAR, amount UBIGINT",
        f"(1,'A',{maximum}),(2,'A',{maximum - 2}),(3,'A',{maximum}),(4,'B',0)",
        "",
        [],
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="attribute-frequency-uint64")
        stage = frequency_read(_stage(bound.relation, "duckdb", distinct=True))
        lowered = lower_query(QueryPlan((stage,), (), stage.stage_id), {"facts": bound.relation})
        output = read_stage(
            PreparedSources(case.session, (), {"facts": bound.relation}, {"facts": bound}),
            lowered.plan.source_read(stage.stage_id),
            lowered.read(stage.stage_id),
        )
        (capture,) = output.attribute_frequencies
        state = dict(capture.groups)[("A",)]
        assert state.dtype == DecimalDataType(20, 0)
        assert state.values == (Decimal(maximum - 2), Decimal(maximum))
        assert state.frequencies == (1, 2)
        assert output.table.loc[output.table["key.0"] == "A", "value"].iloc[0] == 2
        assert dict(output.attribute_state_captures[0].input_types)["value"] == (
            DecimalDataType(20, 0),
        )


def test_frequency_capture_keeps_strict_null_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.semantic.errors import SemanticExpressionError

    with source_case("duckdb", "table", tmp_path, monkeypatch, DATA) as case:
        bound = case.session.bind(case.source, source_identity="attribute-frequency-strict")
        stage = frequency_read(_stage(bound.relation, "duckdb", strict=True, distinct=True))
        lowered = lower_query(QueryPlan((stage,), (), stage.stage_id), {"facts": bound.relation})
        with pytest.raises(SemanticExpressionError, match="non-NA source values"):
            read_stage(
                PreparedSources(case.session, (), {"facts": bound.relation}, {"facts": bound}),
                lowered.plan.source_read(stage.stage_id),
                lowered.read(stage.stage_id),
            )


def test_frequency_capture_retains_original_period_before_grouping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    from marivo.analysis.compiler.graph_plan import PeriodFilter

    data = SourceData(
        "id BIGINT, category VARCHAR, amount DECIMAL(18,2), observed TIMESTAMP",
        "(1,'A',10,TIMESTAMP '2026-01-01'),(2,'A',20,TIMESTAMP '2026-02-01'),(3,'A',1000,TIMESTAMP '2026-03-01')",
        "",
        [],
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="attribute-frequency-period")
        stage = frequency_read(
            replace(
                _stage(bound.relation, "duckdb", distinct=True),
                period=PeriodFilter(
                    "observed",
                    datetime(2026, 2, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 1, tzinfo=timezone.utc),
                ),
            )
        )
        lowered = lower_query(QueryPlan((stage,), (), stage.stage_id), {"facts": bound.relation})
        output = read_stage(
            PreparedSources(case.session, (), {"facts": bound.relation}, {"facts": bound}),
            lowered.plan.source_read(stage.stage_id),
            lowered.read(stage.stage_id),
        )
        (capture,) = output.attribute_frequencies
        state = dict(capture.groups)[("A",)]
        assert state.values == (20,) and state.frequencies == (1,)
        assert state.support_count == 1
        assert output.table["value"].tolist() == [1]


def test_frequency_query_plan_aggregates_one_fact_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    with source_case("duckdb", "table", tmp_path, monkeypatch, DATA) as case:
        bound = case.session.bind(case.source, source_identity="attribute-frequency-plan")
        stage = frequency_read(_stage(bound.relation, "duckdb", distinct=True))
        lowered = lower_query(QueryPlan((stage,), (), stage.stage_id), {"facts": bound.relation})
        read_stage(
            PreparedSources(case.session, (), {"facts": bound.relation}, {"facts": bound}),
            lowered.plan.source_read(stage.stage_id),
            lowered.read(stage.stage_id),
        )
        sql = case.session.submissions[0].sql
        rows = case.session._backend.raw_sql("EXPLAIN " + sql).fetchall()
        plan = "\n".join(str(value) for row in rows for value in row)
        assert plan.count("SEQ_SCAN") == 1
        assert plan.count("HASH_GROUP_BY") == 1
        assert "CROSS_PRODUCT" not in plan and "WINDOW" not in plan
        destination = os.environ.get("MARIVO_SHAPLEY_STATE_EVIDENCE_DIR")
        if destination:
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "frequency-duckdb-query.sql").write_text(sql)
            (directory / "frequency-duckdb-explain.txt").write_text(plan)


def test_actual_shared_frequency_input_retains_separate_q_and_scalar_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with source_case(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        replace(DATA, columns=DATA.columns.replace("DECIMAL(18,2)", "DOUBLE")),
    ) as case:
        bound = case.session.bind(case.source, source_identity="attribute-frequency-share")
        first = frequency_read(
            replace(_stage(bound.relation, "duckdb", q=0.0), binding_id=bound.facts.source_identity)
        )
        assert isinstance(first.program, AttributeFrequencyRead)
        original = first.program.reduction
        assert isinstance(original.node, SourceAggregate)
        assert isinstance(original.node.operand, RowField)
        second = frequency_read(
            replace(
                first,
                stage_id="read:1",
                occurrence=(1,),
                columns=(first.columns[0], replace(first.columns[1], slot_id="other")),
                program=ReductionRead(
                    replace(original, value_slot="other", node=replace(original.node, q=1.0))
                ),
            )
        )
        plan = share_frequency_reads(QueryPlan((first, second), (), first.stage_id))
        assert len(plan.stages) == 1
        lowered = lower_query(plan, {"facts": bound.relation})
        output = read_stage(
            PreparedSources(case.session, (), {"facts": bound.relation}, {"facts": bound}),
            lowered.plan.source_read(first.stage_id),
            lowered.read(first.stage_id),
        )
        assert len(case.session.submissions) == 1
        by_group = output.table.set_index("key.0")
        assert by_group.loc["B", "value"] == 15.0
        assert by_group.loc["B", "other"] == 25.0
        a, b = output.attribute_frequencies
        assert a.groups is b.groups and a.stage_id == b.stage_id
        assert {a.q, b.q} == {0.0, 1.0}
        assert {a.value_slot, b.value_slot} == {"value", "other"}
        (metadata,) = output.attribute_state_captures
        assert metadata.state_entries == 5 and metadata.returned_rows == 5
        assert metadata.value_slots == ("value", "other")
        distinct_scope = replace(second, predicates=(), inputs=("different-parent-scope",))
        unshared = share_frequency_reads(QueryPlan((first, distinct_scope), (), first.stage_id))
        assert len(unshared.stages) == 2
        import operator

        from ibis.common.deferred import BinaryOperator, Just, Variable

        from marivo._definition.expressions import PredicateExpression, RowExpression

        predicate = PredicateExpression(
            Variable("slot_0"),
            (
                RowExpression(
                    BinaryOperator(operator.gt, Variable("slot_0"), Just(20)),
                    (original.node.operand,),
                ),
            ),
        )
        filtered = replace(second, predicates=(predicate,))
        unshared_filter = share_frequency_reads(QueryPlan((first, filtered), (), first.stage_id))
        assert len(unshared_filter.stages) == 2


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
@pytest.mark.parametrize(
    "case_name", ["finite_extremes", "specials", "large_integers", "fractional"]
)
def test_original_native_quantile_finisher_matches_fixed_frequency_endpoints(
    backend: str, profile: str, case_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import math

    from marivo.analysis.core.data_validation import typed_null_mask

    cases: dict[str, list[int | float | None]] = {
        "finite_extremes": [-1e308, 1e308],
        "specials": [float("-inf"), 10.0, float("inf"), float("nan"), None],
        "large_integers": [2**53 + 1, 2**53 + 3],
        "fractional": [0.1, 0.2, 0.4],
    }
    values = cases[case_name]
    if backend == "mysql" and case_name == "specials":
        pytest.skip("MySQL native DOUBLE storage does not admit NaN/Infinity fixture values")
    native_type = (
        "BIGINT"
        if case_name == "large_integers"
        else "DOUBLE PRECISION"
        if backend == "postgres"
        else "DOUBLE"
    )
    literals = []
    for number in values:
        if number is None:
            literals.append("NULL")
        elif isinstance(number, float) and not math.isfinite(number):
            literal = "NaN" if math.isnan(number) else "-Infinity" if number < 0 else "Infinity"
            literals.append(
                "nan()" if math.isnan(number) else "-infinity()" if number < 0 else "infinity()"
            ) if backend == "trino" else literals.append(f"CAST('{literal}' AS DOUBLE)")
            if backend == "postgres":
                literals[-1] = f"CAST('{literal}' AS DOUBLE PRECISION)"
        else:
            literals.append(repr(number))
    data = SourceData(
        f"id BIGINT, category VARCHAR(10), amount {native_type}",
        ",".join(f"({position},'A',{literal})" for position, literal in enumerate(literals, 1)),
        "id Int64, category String, amount Nullable(Int64)"
        if case_name == "large_integers"
        else "id Int64, category String, amount Nullable(Float64)",
        [{"id": index, "category": "A", "amount": value} for index, value in enumerate(values, 1)],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(
            case.source, source_identity="attribute-frequency-native-finisher"
        )
        original = _stage(bound.relation, backend)
        assert isinstance(original.program, ReductionRead)
        reduction = original.program.reduction
        assert isinstance(reduction.node, SourceAggregate)
        probabilities = (0.0, 1.0) if backend == "duckdb" else (0.0, 0.1, 0.25, 0.5, 0.95, 1.0)
        original_reads = tuple(
            replace(
                original,
                stage_id=f"read:{index}",
                occurrence=(index,),
                columns=(
                    original.columns[0],
                    replace(original.columns[1], slot_id=f"value{index}"),
                ),
                program=ReductionRead(
                    replace(
                        reduction, value_slot=f"value{index}", node=replace(reduction.node, q=q)
                    )
                ),
                binding_id=bound.facts.source_identity,
            )
            for index, q in enumerate(probabilities)
        )
        plan = share_frequency_reads(
            QueryPlan(
                tuple(frequency_read(item) for item in original_reads),
                (),
                original_reads[0].stage_id,
            )
        )
        assert len(plan.stages) == 1
        if backend not in ("duckdb", "postgres"):
            with pytest.raises(AnalysisStageUnavailableError):
                lower_query(plan, {"facts": bound.relation})
            assert case.session.submissions == []
            return
        lowered = lower_query(plan, {"facts": bound.relation})
        prepared = PreparedSources(case.session, (), {"facts": bound.relation}, {"facts": bound})
        output = read_stage(
            prepared, lowered.plan.source_read(plan.publish), lowered.read(plan.publish)
        )
        assert {capture.finisher for capture in output.attribute_frequencies} == {
            f"{backend}_native" if backend in ("duckdb", "postgres") else "rank_bounds"
        }
        for index, original_read in enumerate(original_reads):
            reference = lower_query(
                QueryPlan((original_read,), (), original_read.stage_id), {"facts": bound.relation}
            )
            expected = read_stage(
                prepared,
                reference.plan.source_read(original_read.stage_id),
                reference.read(original_read.stage_id),
            )
            slot = f"value{index}"
            actual_value, expected_value = output.table[slot].iloc[0], expected.table[slot].iloc[0]
            assert not typed_null_mask(output.table[slot]).iloc[0]
            assert not typed_null_mask(expected.table[slot]).iloc[0]
            assert actual_value == expected_value or (
                math.isnan(actual_value) and math.isnan(expected_value)
            ), (backend, case_name, probabilities[index], actual_value, expected_value)


@pytest.mark.parametrize("dtype", ["int64", "float64"])
def test_native_duckdb_interpolating_frequency_finisher_is_rejected_before_read(dtype: str) -> None:
    table = ibis.table({"category": "string", "amount": dtype}, name="facts")
    stage = frequency_read(_stage(table, "duckdb"))
    with pytest.raises(AnalysisStageUnavailableError, match="frequency finisher proved equivalent"):
        lower_query(QueryPlan((stage,), (), stage.stage_id), {"facts": table})
