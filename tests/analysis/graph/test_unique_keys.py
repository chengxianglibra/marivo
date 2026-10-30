"""Exact producer facts eliminate scans without weakening unknown relation checks."""

from __future__ import annotations

from unittest.mock import Mock

import ibis
import pandas as pd
import pytest

from marivo.analysis.compiler.unique_keys import relation_unique_keys
from marivo.analysis.core import data_validation
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.core.unique_keys import UniqueKeys
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput


def test_unknown_keyless_stage_does_not_gain_scalar_proof_after_filter() -> None:
    from marivo._definition.query import FilterValues, ValueBinary, ValueLiteral, ValueSlot
    from marivo.analysis.compiler.graph_plan import (
        LocalCompute,
        ProjectionRead,
        QueryPlan,
        SourceRead,
        with_unique_keys,
    )
    from marivo.analysis.compiler.query_program import ResultProgram
    from marivo.analysis.materialization.graph_local_execution import run_local

    columns = (DataColumn("value", "value", ScalarDataType("int64"), False),)
    source = SourceRead(
        "source", "source", (), columns, (), ProjectionRead((("value", "value"),)), (), (), None
    )
    operation = FilterValues(ValueBinary("ge", ValueSlot(0), ValueLiteral(0)))
    filtered = LocalCompute(
        "filtered", ("source",), columns, (), ResultProgram(operation, columns, (), (), (), ())
    )
    plan = with_unique_keys(
        QueryPlan((source, filtered), (("source", ("filtered",)), ("filtered", ())), "filtered")
    )
    stage = plan.stages[-1]
    assert isinstance(stage, LocalCompute)
    assert not stage.unique_keys.proves(())
    rows = StageOutput.from_frame(pd.DataFrame({"value": [1, 2]}), columns, ())
    output = run_local(stage, (rows,))
    assert output.table["value"].tolist() == [1, 2]
    assert not output.unique_keys.proves(())


def test_minimal_candidates_empty_key_and_exact_slot_projection() -> None:
    facts = UniqueKeys(("tenant", "id", "value"), (("id", "tenant"), ("tenant", "id", "value")))
    assert facts.keys == (("tenant", "id"),)
    assert facts.proves(("id", "tenant", "value"))
    assert not facts.proves(("id",))
    assert not UniqueKeys().proves(())
    assert UniqueKeys((), ((),)).proves(())
    assert facts.project(("account", "id"), {"tenant": "account"}).keys == (("account", "id"),)
    assert not facts.project(("id", "value")).keys
    assert facts.fixed(("id", "value"), ("tenant",)).keys == (("id",),)
    assert facts.fixed(("value",), ("tenant", "id")).proves(())
    with pytest.raises(ValueError, match="belong"):
        UniqueKeys(("id",), (("other",),))


def test_nullable_group_producer_skips_scan_but_unknown_null_duplicates_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = ibis.memtable(
        pd.DataFrame({"id": pd.Series([None, None, 1], dtype="Int64"), "value": [10, 20, 4]})
    )
    grouped = table.group_by("id").aggregate(value=table.value.sum())
    selected = grouped.filter(grouped.value > 5).select("id", "value")
    facts = relation_unique_keys(selected)
    rows = selected.execute()
    assert rows["id"].isna().all() and rows["value"].tolist() == [30]
    columns = (
        DataColumn("id", "id", ScalarDataType("int64"), True),
        DataColumn("value", "value", ScalarDataType("int64"), False),
    )
    scan = Mock(wraps=data_validation._validate_stage_keys)
    monkeypatch.setattr(data_validation, "_validate_stage_keys", scan)
    completed = StageOutput.from_frame(rows, columns, ("id",), unique_keys=facts)
    assert completed.unique_keys.proves(("id",))
    scan.assert_not_called()
    with pytest.raises(MaterializationError, match="complete unique stage key"):
        StageOutput.from_frame(
            table.to_pyarrow().to_pandas(types_mapper=pd.ArrowDtype), columns, ("id",)
        )
    assert scan.call_count == 1


def test_distinct_proves_only_actual_columns_and_value_replacement_invalidates() -> None:
    table = ibis.memtable({"id": [1, 1, 1], "value": [10, 20, 10]})
    distinct = table.distinct()
    facts = relation_unique_keys(distinct)
    assert facts.proves(("id", "value")) and not facts.proves(("id",))
    assert distinct.order_by("value").execute()["value"].tolist() == [10, 20]
    projected = distinct.select("id")
    replaced = distinct.mutate(value=ibis.literal(0, type="int64"))
    assert not relation_unique_keys(projected).proves(("id",))
    assert not relation_unique_keys(replaced).proves(("id", "value"))
    columns = (
        DataColumn("id", "id", ScalarDataType("int64"), False),
        DataColumn("value", "value", ScalarDataType("int64"), False),
    )
    with pytest.raises(MaterializationError, match="complete unique stage key"):
        StageOutput.from_frame(
            replaced.execute(), columns, ("id", "value"), unique_keys=relation_unique_keys(replaced)
        )
    union = table.union(table, distinct=True)
    assert relation_unique_keys(union).keys == (("id", "value"),)
    assert not relation_unique_keys(table.union(table, distinct=False)).keys


@pytest.mark.parametrize("null_safe", [False, True])
@pytest.mark.parametrize("condition", ["full", "and", "partial", "range", "or", "transform"])
def test_join_requires_mandatory_complete_compatible_equalities(
    condition: str, null_safe: bool
) -> None:
    left = ibis.memtable(
        pd.DataFrame(
            {
                "row": [1, 2, 3],
                "tenant": pd.Series([1, None, None], dtype="Int64"),
                "id": [10, 10, 99],
            }
        )
    )
    right = ibis.memtable(
        pd.DataFrame(
            {
                "tenant": pd.Series([1, None, 3], dtype="Int64"),
                "id": [10, 10, 10],
                "value": [100, 200, 300],
            }
        )
    )
    seeds = {
        left.op(): UniqueKeys(tuple(left.columns), (("row",),)),
        right.op(): UniqueKeys(tuple(right.columns), (("tenant", "id"),)),
    }
    tenant = left.tenant.identical_to(right.tenant) if null_safe else left.tenant == right.tenant
    identity = left.id.identical_to(right.id) if null_safe else left.id == right.id
    predicate = {
        "full": tenant & identity,
        "and": tenant & identity & (right.value > 0),
        "partial": identity,
        "range": left.id <= right.id,
        "or": (tenant & identity) | identity,
        "transform": (left.tenant == right.tenant % 2) & identity,
    }[condition]
    joined = left.left_join(right, predicate)
    facts = relation_unique_keys(joined, seeds)
    assert facts.proves(("row",)) == (condition in ("full", "and"))
    # Left joins cannot inherit right keys: unmatched left rows all have right NULLs.
    assert not facts.proves(("tenant_right", "id_right"))
    rows = (
        joined.select("row", "value")
        .order_by("row", "value")
        .to_pyarrow()
        .to_pandas(types_mapper=pd.ArrowDtype)
    )
    columns = (
        DataColumn("row", "row", ScalarDataType("int64"), False),
        DataColumn("value", "value", ScalarDataType("int64"), True),
    )
    if condition in ("full", "and"):
        result = StageOutput.from_frame(
            rows, columns, ("row",), unique_keys=facts.project(("row", "value"))
        )
        assert result.table["row"].tolist() == [1, 2, 3]
        assert result.table["value"].iloc[0] == 100
        assert pd.isna(result.table["value"].iloc[2])
    else:
        with pytest.raises(MaterializationError, match="complete unique stage key"):
            StageOutput.from_frame(
                rows, columns, ("row",), unique_keys=facts.project(("row", "value"))
            )


def test_same_named_slots_do_not_share_relation_facts() -> None:
    first = ibis.table({"id": "int64"}, name="first")
    other = ibis.table({"id": "int64"}, name="other")
    seeds = {first.op(): UniqueKeys(("id",), (("id",),))}
    assert relation_unique_keys(first, seeds).proves(("id",))
    assert not relation_unique_keys(other, seeds).proves(("id",))


@pytest.mark.parametrize("null_safe", [False, True])
def test_string_join_without_comparison_proof_keeps_duplicate_validation(null_safe: bool) -> None:
    connection = ibis.duckdb.connect()
    try:
        connection.raw_sql("CREATE TABLE collation_left (row BIGINT, x VARCHAR COLLATE NOCASE)")
        connection.raw_sql("INSERT INTO collation_left VALUES (1, 'a')")
        connection.raw_sql("CREATE TABLE collation_right (key VARCHAR)")
        connection.raw_sql("INSERT INTO collation_right VALUES ('A'), ('a')")
        left = connection.table("collation_left")
        right = connection.table("collation_right").distinct()
        predicate = left.x.identical_to(right.key) if null_safe else left.x == right.key
        joined = left.left_join(right, predicate)
        facts = relation_unique_keys(
            joined, {left.op(): UniqueKeys(tuple(left.columns), (("row",),))}
        )
        assert not facts.proves(("row",))
        rows = joined.execute()
        assert rows["row"].tolist() == [1, 1]
        columns = (
            DataColumn("row", "row", ScalarDataType("int64"), False),
            DataColumn("x", "x", ScalarDataType("string"), False),
            DataColumn("key", "key", ScalarDataType("string"), True),
        )
        with pytest.raises(MaterializationError, match="complete unique stage key"):
            StageOutput.from_frame(rows, columns, ("row",), unique_keys=facts)
    finally:
        connection.disconnect()


def test_generic_keyless_stage_remains_unknown_but_complete_frame_checks_cardinality() -> None:
    columns = (DataColumn("value", "value", ScalarDataType("int64"), False),)
    rows = pd.DataFrame({"value": [1, 2]})
    output = StageOutput.from_frame(rows, columns, ())
    assert output.table["value"].tolist() == [1, 2]
    assert not output.unique_keys.proves(())
    with pytest.raises(MaterializationError, match="zero-axis Frame"):
        data_validation.normalize_complete_frame(rows, columns, ())
