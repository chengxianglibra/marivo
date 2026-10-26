"""Checked target reports share a union domain without filling absent values."""

from decimal import Decimal

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.data_schema import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_attribute_merge import (
    AttributeMergeProgram,
    AttributeMergeValues,
    infer_attribute_merge_schema,
)
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.dsl.frame_attribute_merge import merge_attribute_reports
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.methods.frame_attribute_merge import merge_attribute_data
from tests.shared_fixtures import DslCaseFactory, catalog_value


def test_union_preserves_exact_joint_null_keys_and_per_target_missingness() -> None:
    key = DataColumn("key", "key", ScalarDataType("int64"), True)
    group = DataColumn("group", "group", ScalarDataType("string"), False)
    a = DataColumn("a", "a.contribution", DecimalDataType(38, 0), True)
    b = DataColumn("b", "b.contribution", DecimalDataType(38, 0), True)
    present = DataColumn("present", "a.present", ScalarDataType("boolean"), False)
    other_present = DataColumn("present", "b.present", ScalarDataType("boolean"), False)
    axes = (DataAxis("joint", (key, group), name="joint"),)
    schemas = (
        DataSchema(
            axes,
            (a,),
            (present,),
            value_paths=(("a", "contribution"),),
            presence_paths=(("a", "present"),),
        ),
        DataSchema(
            axes,
            (b,),
            (other_present,),
            value_paths=(("b", "contribution"),),
            presence_paths=(("b", "present"),),
        ),
    )
    large = 2**60 + 1
    left = pd.DataFrame(
        {
            "key": pd.Series([large, None], dtype="int64[pyarrow]"),
            "group": ["x", "n"],
            "a": [Decimal(2), Decimal(-1)],
            "present": [True, True],
        }
    )
    right = pd.DataFrame(
        {
            "key": pd.Series([large, large], dtype="int64[pyarrow]"),
            "group": ["y", "x"],
            "b": [Decimal(5), None],
            "present": [True, True],
        }
    )
    inputs = tuple(
        StageOutput.from_frame(table, schema.columns, schema.key_fields, frame_schema=schema)
        for table, schema in zip((left, right), schemas, strict=True)
    )
    output = infer_attribute_merge_schema(schemas)
    result, attachments = merge_attribute_data(
        AttributeMergeProgram(AttributeMergeValues(), schemas, output), inputs
    )
    assert tuple(result.columns) == tuple(c.slot_id for c in output.columns)
    assert attachments == ()
    assert result["key"].iloc[0] == large
    assert result["key"].isna().tolist() == [False, True, False]
    assert result["group"].tolist() == ["x", "n", "y"]
    assert pa.array(result[output.values[0].slot_id]).to_pylist() == [Decimal(2), Decimal(-1), None]
    assert pa.array(result[output.values[1].slot_id]).to_pylist() == [None, None, Decimal(5)]
    assert result[output.presence[0].slot_id].tolist() == [True, True, False]
    assert result[output.presence[1].slot_id].tolist() == [True, False, True]


def test_merge_uses_existing_frame_schedule_for_fixed_inputs(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    region = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    left = case.session.values(
        by=(region,), columns={"a": "CNY"}, rows=((("east",), {"a": 2}),)
    ).compute()
    right = case.session.values(
        by=(region,), columns={"b": "CNY"}, rows=((("west",), {"b": 3}),)
    ).compute()
    merged = merge_attribute_reports((left, right)).compute()
    assert merged.output_names == ("a", "b")
    assert isinstance(merged._backing, MemoryFrameBacking)
    rows = merged._backing.data.copy_rows()
    assert len(rows) == 2
    assert rows[merged._schema.values[0].slot_id].isna().tolist() == [False, True]
    assert rows[merged._schema.values[1].slot_id].isna().tolist() == [True, False]


def test_union_admits_null_key_from_another_target_without_changing_key_type() -> None:
    from dataclasses import replace

    key = DataColumn("key", "key", ScalarDataType("int64"), False)
    a = DataColumn("a", "a", ScalarDataType("int64"), False)
    b = DataColumn("b", "b", ScalarDataType("int64"), False)
    left = DataSchema((DataAxis("key", (key,), name="key"),), (a,), value_paths=(("a",),))
    right = DataSchema(
        (DataAxis("key", (replace(key, nullable=True),), name="key"),), (b,), value_paths=(("b",),)
    )
    schemas = (left, right)
    output = infer_attribute_merge_schema(schemas)
    assert output.axes[0].columns[0].nullable
    assert output.axes[0].columns[0].dtype == ScalarDataType("int64")
    tables = (
        pd.DataFrame({"key": pd.Series([2**60 + 1], dtype="int64[pyarrow]"), "a": [2]}),
        pd.DataFrame({"key": pd.Series([None], dtype="int64[pyarrow]"), "b": [3]}),
    )
    inputs = tuple(
        StageOutput.from_frame(table, schema.columns, schema.key_fields, frame_schema=schema)
        for schema, table in zip(schemas, tables, strict=True)
    )
    rows, _ = merge_attribute_data(
        AttributeMergeProgram(AttributeMergeValues(), schemas, output), inputs
    )
    assert pa.array(rows["key"]).to_pylist() == [2**60 + 1, None]
